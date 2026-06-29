from pathlib import Path
import argparse, csv, sys
from sqlalchemy import text
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db import get_engine

REPORTS = {
 "queue_membership": "SELECT q.name queue_name,q.division_name,COUNT(qm.user_id) members,SUM(CASE WHEN qm.joined=1 THEN 1 ELSE 0 END) joined_members FROM gc_queues q LEFT JOIN gc_queue_members qm ON q.id=qm.queue_id GROUP BY q.id,q.name,q.division_name ORDER BY q.name",
 "phones_by_site": "SELECT site_name, COUNT(*) phones FROM gc_phones GROUP BY site_name ORDER BY site_name",
 "users_by_division": "SELECT division_name, COUNT(*) users FROM gc_users GROUP BY division_name ORDER BY division_name",
 "conversation_summary": "SELECT media_type,direction,COUNT(*) conversations FROM gc_conversations GROUP BY media_type,direction ORDER BY conversations DESC"
}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--report", choices=sorted(REPORTS), required=True); ap.add_argument("--out")
    args=ap.parse_args()
    out=Path(args.out) if args.out else ROOT/"outputs"/f"{args.report}.csv"
    with get_engine().connect() as conn:
        rows=conn.execute(text(REPORTS[args.report])).mappings().all()
    with out.open("w", newline="", encoding="utf-8") as f:
        if rows:
            w=csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows([dict(r) for r in rows])
        else:
            f.write("no_data\\n")
    print(f"[OK] Wrote report: {out}")
if __name__ == "__main__": main()
