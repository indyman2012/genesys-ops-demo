from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genesys_client import GenesysClient, safe_str
from db import get_engine

def log(level, msg):
    prefix = {"ok":"[OK]","warn":"[WARN]","error":"[ERROR]","dry":"[DRY RUN]","info":"[INFO]"}.get(level,"[INFO]")
    print(f"{prefix} {msg}", flush=True)

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def jd(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)

def fit(value, limit=512):
    value = safe_str(value)
    return value[:limit] if len(value) > limit else value


def upsert_users(gc, apply):
    users = gc.paged_get("/api/v2/users")
    if not apply:
        log("dry", f"Would upsert {len(users)} users")
        return
    with get_engine().begin() as conn:
        for u in users:
            d = u.get("division") if isinstance(u.get("division"), dict) else {}
            conn.execute(text("""INSERT INTO gc_users
            (id,name,email,username,title,division_id,division_name,state,raw_json,collected_at)
            VALUES (:id,:name,:email,:username,:title,:division_id,:division_name,:state,CAST(:raw AS JSON),:ts)
            ON DUPLICATE KEY UPDATE name=VALUES(name),email=VALUES(email),username=VALUES(username),title=VALUES(title),
            division_id=VALUES(division_id),division_name=VALUES(division_name),state=VALUES(state),raw_json=VALUES(raw_json),collected_at=VALUES(collected_at)"""),
            {"id":u.get("id"),"name":u.get("name"),"email":u.get("email"),"username":u.get("username"),"title":u.get("title"),"division_id":d.get("id"),"division_name":d.get("name"),"state":u.get("state"),"raw":jd(u),"ts":now()})
    log("ok", f"Upserted {len(users)} users")

def upsert_queues(gc, apply):
    queues = gc.paged_get("/api/v2/routing/queues")
    if not apply:
        log("dry", f"Would upsert {len(queues)} queues and collect members")
        return
    with get_engine().begin() as conn:
        for q in queues:
            d = q.get("division") if isinstance(q.get("division"), dict) else {}
            conn.execute(text("""INSERT INTO gc_queues
            (id,name,division_id,division_name,member_count,joined_member_count,raw_json,collected_at)
            VALUES (:id,:name,:division_id,:division_name,:member_count,:joined_member_count,CAST(:raw AS JSON),:ts)
            ON DUPLICATE KEY UPDATE name=VALUES(name),division_id=VALUES(division_id),division_name=VALUES(division_name),
            member_count=VALUES(member_count),joined_member_count=VALUES(joined_member_count),raw_json=VALUES(raw_json),collected_at=VALUES(collected_at)"""),
            {"id":q.get("id"),"name":q.get("name"),"division_id":d.get("id"),"division_name":d.get("name"),"member_count":q.get("memberCount"),"joined_member_count":q.get("joinedMemberCount"),"raw":jd(q),"ts":now()})
            try:
                members = gc.paged_get(f"/api/v2/routing/queues/{q['id']}/members")
            except Exception as exc:
                log("warn", f"Member collection failed for {q.get('name')}: {exc}")
                members = []
            for m in members:
                u = m.get("user") if isinstance(m.get("user"), dict) else {}
                uid = m.get("id") or u.get("id")
                if not uid:
                    continue
                conn.execute(text("""INSERT INTO gc_queue_members
                (queue_id,user_id,queue_name,user_name,joined,raw_json,collected_at)
                VALUES (:qid,:uid,:qname,:uname,:joined,CAST(:raw AS JSON),:ts)
                ON DUPLICATE KEY UPDATE queue_name=VALUES(queue_name),user_name=VALUES(user_name),joined=VALUES(joined),raw_json=VALUES(raw_json),collected_at=VALUES(collected_at)"""),
                {"qid":q.get("id"),"uid":uid,"qname":q.get("name"),"uname":m.get("name") or u.get("name"),"joined":m.get("joined"),"raw":jd(m),"ts":now()})
    log("ok", f"Upserted {len(queues)} queues and queue members")

def upsert_telephony(gc, apply):
    locs = gc.paged_get("/api/v2/locations")
    sites = gc.paged_get("/api/v2/telephony/providers/edges/sites")
    phones = gc.paged_get("/api/v2/telephony/providers/edges/phones")
    if not apply:
        log("dry", f"Would upsert {len(locs)} locations, {len(sites)} sites, {len(phones)} phones")
        return
    with get_engine().begin() as conn:
        for loc in locs:
            em = loc.get("emergencyNumber") if isinstance(loc.get("emergencyNumber"), dict) else {}
            conn.execute(text("""INSERT INTO gc_locations (id,name,emergency_number,address_json,raw_json,collected_at)
            VALUES (:id,:name,:em,CAST(:addr AS JSON),CAST(:raw AS JSON),:ts)
            ON DUPLICATE KEY UPDATE name=VALUES(name),emergency_number=VALUES(emergency_number),address_json=VALUES(address_json),raw_json=VALUES(raw_json),collected_at=VALUES(collected_at)"""),
            {"id":loc.get("id"),"name":loc.get("name"),"em":em.get("e164") or em.get("number"),"addr":jd(loc.get("address") or {}),"raw":jd(loc),"ts":now()})
        for site in sites:
            l = site.get("location") if isinstance(site.get("location"), dict) else {}
            conn.execute(text("""INSERT INTO gc_sites (id,name,location_id,location_name,state,raw_json,collected_at)
            VALUES (:id,:name,:lid,:lname,:state,CAST(:raw AS JSON),:ts)
            ON DUPLICATE KEY UPDATE name=VALUES(name),location_id=VALUES(location_id),location_name=VALUES(location_name),state=VALUES(state),raw_json=VALUES(raw_json),collected_at=VALUES(collected_at)"""),
            {"id":site.get("id"),"name":site.get("name"),"lid":l.get("id"),"lname":l.get("name"),"state":site.get("state"),"raw":jd(site),"ts":now()})
        for p in phones:
            s = p.get("site") if isinstance(p.get("site"), dict) else {}
            b = p.get("phoneBaseSettings") if isinstance(p.get("phoneBaseSettings"), dict) else {}
            conn.execute(text("""INSERT INTO gc_phones (id,name,site_id,site_name,phone_base_id,raw_json,collected_at)
            VALUES (:id,:name,:sid,:sname,:bid,CAST(:raw AS JSON),:ts)
            ON DUPLICATE KEY UPDATE name=VALUES(name),site_id=VALUES(site_id),site_name=VALUES(site_name),phone_base_id=VALUES(phone_base_id),raw_json=VALUES(raw_json),collected_at=VALUES(collected_at)"""),
            {"id":p.get("id"),"name":p.get("name"),"sid":s.get("id"),"sname":s.get("name"),"bid":b.get("id"),"raw":jd(p),"ts":now()})
    log("ok", "Upserted telephony inventory")

def iter_date_chunks(days: int, max_days: int = 7):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=max_days), end)
        yield cursor, chunk_end
        cursor = chunk_end

def collect_conversations_for_interval(gc, start, end, apply):
    interval = f"{start.isoformat().replace('+00:00','Z')}/{end.isoformat().replace('+00:00','Z')}"
    body = {"interval": interval, "paging": {"pageSize": 100, "pageNumber": 1}, "order": "asc", "orderBy": "conversationStart"}

    if not apply:
        log("dry", f"Would collect conversations for {interval}")
        return 0

    total = 0
    page = 1
    while True:
        body["paging"]["pageNumber"] = page
        payload = gc.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
        convs = payload.get("conversations", []) if isinstance(payload, dict) else []
        if not convs:
            break

        with get_engine().begin() as conn:
            for c in convs:
                media = direction = ani = dnis = ""
                qids, agents, wraps = [], [], []
                for part in c.get("participants") or []:
                    if part.get("purpose") == "agent" and part.get("userId"):
                        agents.append(part.get("userId"))
                    for sess in part.get("sessions") or []:
                        media = media or safe_str(sess.get("mediaType"))
                        direction = direction or safe_str(sess.get("direction"))
                        ani = ani or safe_str(sess.get("ani"))
                        dnis = dnis or safe_str(sess.get("dnis"))
                        if sess.get("queueId"):
                            qids.append(sess.get("queueId"))
                        for seg in sess.get("segments") or []:
                            if seg.get("queueId"):
                                qids.append(seg.get("queueId"))
                            if seg.get("wrapUpCode"):
                                wraps.append(seg.get("wrapUpCode"))

                conn.execute(text("""INSERT INTO gc_conversations
                (conversation_id,conversation_start,conversation_end,media_type,direction,ani,dnis,queue_ids,agent_ids,wrapup_codes,raw_json,collected_at)
                VALUES (:id,:start,:end,:media,:direction,:ani,:dnis,:qids,:agents,:wraps,CAST(:raw AS JSON),:ts)
                ON DUPLICATE KEY UPDATE conversation_start=VALUES(conversation_start),conversation_end=VALUES(conversation_end),media_type=VALUES(media_type),direction=VALUES(direction),ani=VALUES(ani),dnis=VALUES(dnis),queue_ids=VALUES(queue_ids),agent_ids=VALUES(agent_ids),wrapup_codes=VALUES(wrapup_codes),raw_json=VALUES(raw_json),collected_at=VALUES(collected_at)"""),
                {"id":c.get("conversationId"),
                 "start":safe_str(c.get("conversationStart")).replace("T"," ").replace("Z","")[:19] or None,
                 "end":safe_str(c.get("conversationEnd")).replace("T"," ").replace("Z","")[:19] or None,
                 "media":media, "direction":direction, "ani":fit(ani), "dnis":fit(dnis),
                 "qids":",".join(sorted(set(qids))), "agents":",".join(sorted(set(agents))), "wraps":",".join(sorted(set(wraps))),
                 "raw":jd(c), "ts":now()})
        total += len(convs)
        log("ok", f"Collected interval {interval} page {page}: {len(convs)} conversations")
        if len(convs) < 100:
            break
        page += 1

    return total

def collect_conversations(gc, days, apply):
    grand_total = 0
    for start, end in iter_date_chunks(days, max_days=7):
        grand_total += collect_conversations_for_interval(gc, start, end, apply)
    log("ok", f"Collected {grand_total} conversations across <=7-day chunks")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", action="store_true")
    ap.add_argument("--queues", action="store_true")
    ap.add_argument("--telephony", action="store_true")
    ap.add_argument("--conversations", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--days-back", type=int, default=7)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    gc = GenesysClient(ROOT, log)
    gc.authenticate()

    if not any([args.users,args.queues,args.telephony,args.conversations,args.all]):
        args.all = True

    if args.all or args.users:
        upsert_users(gc, args.apply)
    if args.all or args.queues:
        upsert_queues(gc, args.apply)
    if args.all or args.telephony:
        upsert_telephony(gc, args.apply)
    if args.all or args.conversations:
        collect_conversations(gc, args.days_back, args.apply)

    log("ok", "Collection complete")

if __name__ == "__main__":
    main()
