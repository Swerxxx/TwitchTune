"""Normalize paid messages without treating donor nicknames as Twitch IDs."""
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from albums import album_reference
from core import parse_link, telegram_delivery_note

PROVIDERS={'donationalerts':'DonationAlerts','donatex':'DonateX'}


def event_time(value):
    try:
        if isinstance(value,(int,float)):
            return float(value) if 0<float(value)<1e11 else 0.
        stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()
    except (ValueError,TypeError,OverflowError):return 0.


def flag(value):
    if isinstance(value,str):return value.strip().lower() not in ('','0','false','no','off','null','none')
    return bool(value)


def normalize_event(provider,account,data,include_tests=False):
    return normalize_event_checked(provider,account,data,include_tests)[0]


def normalize_event_checked(provider,account,data,include_tests=False):
    if provider not in PROVIDERS or not isinstance(data,dict):return None,'Неизвестный сервис или неверный формат сообщения.'
    ident=str(data.get('id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ident):return None,'Некорректный ID сообщения: '+str(data.get('id'))[:100]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(account)):return None,'Некорректный ID подключённого аккаунта.'
    if flag(data.get('isPotentiallyUnsafe')) or flag(data.get('is_potentially_unsafe')):return None,'Сервис пометил сообщение небезопасным.'
    test=flag(data.get('isTest')) or flag(data.get('is_test'))
    if test and not include_tests:return None,'Тестовое сообщение: приём тестов выключен.'
    name=data.get('name')
    accepted={'donation','donations'} if provider=='donationalerts' else {'donation'}
    if name is not None and (not isinstance(name,str) or name.strip().casefold() not in accepted):
        return None,'Тип события: '+str(name)[:100]+' (ожидался donation'+('/Donations' if provider=='donationalerts' else '')+').'
    try:
        amount=Decimal(str(data.get('amount','0')))
        if not amount.is_finite() or amount<0 or (amount==0 and not (test and include_tests)):return None,'Сумма должна быть положительным числом.'
    except (ValueError,InvalidOperation):return None,'Неверный формат суммы: '+str(data.get('amount'))[:100]
    message=str(data.get('message') or '')[:10000]
    if telegram_delivery_note(message):return None,'Пометка «в тг»: такие заказы пропускаются.'
    # DonateX musicLink is an explicit song order, independent of donor text.
    music=str(data.get('musicLink') or '').strip()
    text=music or message
    links=re.findall(r'https?://[^\s<>"\x00-\x1f]+',text)
    if len(links)!=1:return None,'Ожидалась одна поддерживаемая ссылка.'
    url=links[0].rstrip('.,!;)')
    try:
        if not album_reference(url):url,_=parse_link(url)
    except ValueError:return None,'Неподдерживаемая или некорректная ссылка.'
    currency=str(data.get('currency') or '').strip().upper()
    if not re.fullmatch(r'[A-Z]{3}',currency):return None,'Неверный код валюты: '+repr(data.get('currency'))[:100]
    return dict(event_key=f'donation:{provider}:{account}:{ident}',provider=provider,account=str(account),
        event_id=ident,url=url,viewer=str(data.get('username') or 'Аноним').strip()[:100] or 'Аноним',
        amount=str(amount),currency=currency,created_at=event_time(data.get('timestamp') or data.get('created_at')) or time.time(),is_test=test),''


def track_price(value=200):
    try:return max(1,min(1000000,int(value)))
    except (TypeError,ValueError,OverflowError):return 200


def priced_message(provider,account,data,price=200,include_tests=False):
    return priced_message_checked(provider,account,data,price,include_tests)[0]


def priced_message_checked(provider,account,data,price=200,include_tests=False):
    """Capture one receipt and its price before any asynchronous album lookup."""
    if not isinstance(data,dict):return None,'Неверный формат сообщения.'
    message=str(data.get('message') or '')[:10000]
    if telegram_delivery_note(message):return None,'Пометка «в тг»: такие заказы пропускаются.'
    music=str(data.get('musicLink') or '').strip()
    urls=[]
    for link in re.findall(r'https?://[^\s<>"\x00-\x1f]+',music or message):
        url=link.rstrip('.,!;)')
        try:
            reference=album_reference(url)
            url=reference[1] if reference else parse_link(url)[0]
        except ValueError:continue
        if url not in urls:urls.append(url)
    if not urls:return None,('Ссылка не поддерживается или имеет неверный формат.' if re.search(r'https?://',music or message,re.I) else 'В тексте сообщения нет ссылки https:// или http://.')
    raw=dict(data,message=urls[0],musicLink='')
    event,reason=normalize_event_checked(provider,account,raw,include_tests)
    if not event:return None,reason
    price=track_price(price)
    amount=Decimal(event['amount'])
    slots=(10000 if amount>=Decimal(price)*10000 else int(amount//price)) if event['currency']=='RUB' else 0
    note='' if slots else ('Принимаются заказы только в RUB; пересчёт валют не настроен.' if event['currency']!='RUB'
                            else f'Сумма ниже минимума {price} ₽.')
    return dict(event,order_urls=urls,unit_price=price,paid_slots=slots,pricing_note=note),note


def allocate_orders(receipt,album_loader=None):
    """Each song costs one slot; albums are accepted whole or skipped whole."""
    remaining=receipt['paid_slots'];orders=[]
    for index,url in enumerate(receipt['order_urls'],1):
        if remaining<=0:break
        metadata=None;count=1
        if album_reference(url):
            if album_loader is None:raise ValueError('Нужно прочитать состав альбома.')
            metadata=album_loader(url)
            count=len(metadata.get('tracks') or [])
            if not count:raise ValueError('В альбоме нет треков.')
        if count>remaining:continue
        child={k:v for k,v in receipt.items() if k not in ('order_urls','pricing_note')}
        child.update(event_key=receipt['event_key']+f':track:{index}',url=url,paid_tracks=count)
        if metadata is not None:child['album_metadata']=metadata
        orders.append(child);remaining-=count
    return orders
