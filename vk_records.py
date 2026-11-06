"""Bounded parsing of VK audio metadata and playback envelopes; no JS execution."""
import json
import re
from html.parser import HTMLParser


def audio_object(data):
    if isinstance(data,list) and len(data)>=6:
        original=data
        data=dict(zip(('id','owner_id','url','title','performer','duration'),original[:6]))
        if len(original)>13 and isinstance(original[13],str):
            hashes=original[13].split('/')
            if len(hashes)>5:data.update(actionHash=hashes[2],urlHash=hashes[5])
        if len(original)>14:data['coverUrl']=original[14]
    if not isinstance(data,dict):return None
    data=dict(data)
    if 'owner_id' not in data and 'ownerId' in data:data['owner_id']=data['ownerId']
    full=data.get('full_id') or data.get('fullId')
    if isinstance(full,str) and re.fullmatch(r'-?\d+_\d+',full):
        owner,ident=full.split('_')
        if str(data.get('owner_id',owner))!=owner or str(data.get('id',ident))!=ident:return None
        data['owner_id'],data['id']=owner,ident
    if not data.get('coverUrl') and isinstance(data.get('album'),dict):
        thumb=data['album'].get('thumb')
        if isinstance(thumb,dict):data['coverUrl']=next((thumb[x] for x in ('photo_1200','photo_600','photo_300','photo_135','photo_68') if isinstance(thumb.get(x),str) and thumb[x].startswith('https://')), '')
    if not re.fullmatch(r'\d+',str(data.get('id',''))) or not re.fullmatch(r'-?\d+',str(data.get('owner_id',''))):return None
    return data


def structured_records(value):
    """Select candidates from known envelopes; caller still checks exact track ID."""
    records=[];pending=[(value,0)];visited=0
    while pending and visited<5000:
        node,depth=pending.pop();visited+=1
        if depth>20:continue
        record=audio_object(node)
        if record and 'id' in record and 'owner_id' in record:
            records.append(record)
            continue
        if isinstance(node,list):pending.extend((x,depth+1) for x in reversed(node[:1000]))
        elif isinstance(node,dict):
            pending.extend((x,depth+1) for x in list(node.values())[:1000] if isinstance(x,(dict,list)))
    return records


class AudioPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records=[];self.script=None;self.json_script=False;self.script_size=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        value=attrs.get('data-audio')
        if value:
            try:
                record=audio_object(json.loads(value))
                if record:self.records.append(record)
            except (ValueError,TypeError):pass
        if tag=='script':
            self.script=[];self.script_size=0
            self.json_script=attrs.get('type','').lower() in ('application/json','application/ld+json') or attrs.get('id')=='__NEXT_DATA__'
    def handle_data(self,data):
        if self.script is not None:
            self.script_size+=len(data)
            if self.script_size<=2_000_000:self.script.append(data)
    def handle_endtag(self,tag):
        if tag!='script' or self.script is None:return
        text=''.join(self.script);self.script=None
        if self.script_size>2_000_000:return
        if self.json_script:
            try:self.records.extend(structured_records(json.loads(text)))
            except (ValueError,TypeError):pass
        # Public VK web client initializes audio data through new AudioPage(...).
        # Decode JSON arguments only, never evaluate arbitrary JavaScript.
        for match in list(re.finditer(r'\bnew\s+AudioPage\s*\(\s*(?=[{\[])',text))[:10]:
            try:
                value,_=json.JSONDecoder().raw_decode(text,match.end())
                self.records.extend(structured_records(value))
            except (ValueError,TypeError):pass
