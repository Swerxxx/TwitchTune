"""Durable acknowledgements of listened Twitch Channel Points requests."""
import time
from twitch import TwitchAPIError
from browser_bridge import migrate


class TwitchCompletions:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS twitch_completions (
                redemption TEXT PRIMARY KEY, reward TEXT, broadcaster TEXT, client TEXT,
                state TEXT DEFAULT 'waiting', retry_at REAL DEFAULT 0, error TEXT DEFAULT '')''')
            migrate(store.db)

    def bind(self, event, client_id, broadcaster=''):
        redemption = event.get('id')
        reward = (event.get('reward') or {}).get('id')
        broadcaster = event.get('broadcaster_user_id') or broadcaster
        if not all(isinstance(x, str) and x for x in (redemption, reward, broadcaster, client_id)):
            return
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO twitch_completions(redemption,reward,broadcaster,client) VALUES(?,?,?,?)',
                                  (redemption, reward, broadcaster, client_id))
            self.store.db.execute("UPDATE twitch_completions SET reward=COALESCE(NULLIF(reward,''),?),viewer_login=?,user_input=?,reward_title=?,channel_login=? WHERE redemption=? AND broadcaster=? AND client=? AND viewer_login=''",
                (reward,str(event.get('user_login') or event.get('user_name') or '')[:200],
                 str(event.get('user_input') or '')[:4000],str((event.get('reward') or {}).get('title') or '')[:300],
                 str(event.get('broadcaster_user_login') or '').lower()[:100],redemption,broadcaster,client_id))

    def adopt_browser_history(self, client, reward_ids):
        if not reward_ids:
            return
        selected = ','.join('?' for _ in reward_ids)
        with self.store.lock, self.store.db:
            self.store.db.execute(f'''INSERT OR IGNORE INTO twitch_completions(redemption,reward,broadcaster,client,state)
                SELECT redemption,reward_id,?,?,'pending' FROM queue
                WHERE redemption IS NOT NULL AND redemption!='' AND redemption NOT LIKE 'donation:%'
                AND rating IS NOT NULL AND status='Прослушано' AND completion_status='Прослушано'
                AND reward_id IN ({selected})''',
                (client.user['user_id'], client.client_id, *reward_ids))

    def listened(self, row, client=None):
        if not row or not row.get('redemption') or row['redemption'].startswith('donation:'):
            return
        # Older queues can be adopted only with a known reward ID and account.
        if client and client.user and row.get('reward_id'):
            self.bind(dict(id=row['redemption'],reward={'id':row['reward_id']}),
                      client.client_id,client.user.get('user_id',''))
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE twitch_completions SET state=?,retry_at=0 WHERE redemption=? AND state='waiting'",
                                  ('pending' if row.get('rating') is not None else 'awaiting_rating',row['redemption']))

    def remote_status(self, event):
        status = str(event.get('status') or '').upper()
        state = {'FULFILLED':'fulfilled','CANCELED':'canceled','CANCELLED':'canceled'}.get(status)
        if not state or not isinstance(event.get('id'),str):
            return False
        with self.store.lock, self.store.db:
            cursor=self.store.db.execute('UPDATE twitch_completions SET state=?,lease_until=0 WHERE redemption=? AND broadcaster=?',
                (state,event['id'],str(event.get('broadcaster_user_id') or '')))
        return bool(cursor.rowcount)

    def resume(self):
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE twitch_completions SET state='pending',retry_at=0 WHERE state='blocked'")

    def flush(self, client):
        if not client or not client.user or not client.tokens:
            return []
        with self.store.lock:
            rows = [dict(r) for r in self.store.db.execute("SELECT c.* FROM twitch_completions c LEFT JOIN queue q ON q.redemption=c.redemption WHERE c.state IN ('pending','awaiting_rating') AND c.retry_at<=? AND c.client=? AND c.broadcaster=? AND COALESCE(c.reward,'')!='' AND (q.rating IS NOT NULL OR q.id IS NULL) LIMIT 10",
                (time.time(),client.client_id,client.user.get('user_id','')))]
        notices = []
        for row in rows:
            queue_row = self.store.get_redemption(row['redemption'])
            if not queue_row:
                self.set_result(row,'discarded')
                continue
            try:
                status = client.fulfill_redemption(row['reward'],row['redemption'])
                if status not in ('FULFILLED','CANCELED'):
                    raise RuntimeError('Twitch не подтвердил статус заказа.')
                self.set_result(row,'fulfilled' if status=='FULFILLED' else 'canceled')
                notices.append('Заказ Twitch отмечен выполненным.' if status=='FULFILLED' else 'Заказ уже отменён на Twitch.')
            except Exception as exc:
                permanent = isinstance(exc,TwitchAPIError) and exc.status in (400,401,403,404)
                error = str(exc)[:350]
                self.set_result(row,'blocked' if permanent else 'pending',error,time.time()+60)
                if error != row['error']:
                    notices.append('Не удалось отметить заказ Twitch: '+error)
        return notices

    def set_result(self, row, state, error='', retry_at=0):
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE twitch_completions SET state=?,error=?,retry_at=? WHERE redemption=?',
                                  (state,error,retry_at,row['redemption']))
