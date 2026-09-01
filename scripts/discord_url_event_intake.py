#!/usr/bin/env python3
"""Discord URL eventをdurable queue経由でDCB snapshotへ接続する。"""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, subprocess, sys, time, uuid
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; SRC=ROOT/"src"; sys.path.insert(0,str(SRC)) if str(SRC) not in sys.path else None
from discord_context_bridge.core import DEFAULT_TEXT_SNAPSHOT_STORE, build_bridge_intake, plan_discord_url_read  # noqa:E402
from discord_context_bridge.credentials import split_secret_command  # noqa:E402
EVENT_SOURCES={"prompt_url_received","gateway_message_create","gateway_message_update","rest_reconciliation","chrome_tab_updated","chrome_dom_changed"}; MAX_INPUT_BYTES=5_000_000
def default_state_path()->Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()/".local"/"state")/"discord-context-bridge"/"url-event-intake.sqlite3"
def _connect(path:Path)->sqlite3.Connection:
    path.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(path,timeout=10,isolation_level=None); c.execute("PRAGMA busy_timeout=10000")
    c.execute("""CREATE TABLE IF NOT EXISTS url_events(event_key TEXT PRIMARY KEY,job_id TEXT NOT NULL,event_source TEXT NOT NULL,target_url TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN('pending','processing','completed')),content_hash TEXT NOT NULL DEFAULT'',owner TEXT NOT NULL DEFAULT'',lease_expires_at REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0)"""); return c
def _key(event_id:str,url:str,source:str)->str: return hashlib.sha256(f"{source}\0{event_id}\0{url}".encode()).hexdigest()
def _base(source:str,url:str)->dict[str,Any]: return {"schema":"discord_url_event_intake.v1","ok":False,"event_source":source if source in EVENT_SOURCES else "invalid","url_present":bool(url),"url_output":"omitted","raw_text_returned":False,"participant_names_returned":False,"path_output":"omitted","outbound_actions":"disabled"}
def _complete(c:sqlite3.Connection,*,key:str,job_id:str,url:str,source:str,text:str,snapshot_store:Path,owner:str)->dict[str,Any]:
    base=_base(source,url)
    try: intake=build_bridge_intake(url=url,text=text.strip(),source=source,snapshot_store=snapshot_store)
    except (OSError,UnicodeError,ValueError):
        c.execute("UPDATE url_events SET status='pending',owner='',lease_expires_at=0 WHERE event_key=? AND owner=?",(key,owner)); return {**base,"decision":"blocked","reason":"snapshot_save_failed","job_id":job_id}
    if not intake.get("snapshot",{}).get("saved"):
        c.execute("UPDATE url_events SET status='pending',owner='',lease_expires_at=0 WHERE event_key=? AND owner=?",(key,owner)); return {**base,"decision":"blocked","reason":"snapshot_save_failed","job_id":job_id}
    updated=c.execute("UPDATE url_events SET status='completed',content_hash=?,owner='',lease_expires_at=0 WHERE event_key=? AND owner=?",(str(intake["snapshot"].get("content_hash") or ""),key,owner))
    if updated.rowcount != 1: return {**base,"decision":"blocked","reason":"stale_worker_lease_lost","job_id":job_id}
    return {**base,"ok":True,"decision":"snapshot_refreshed","reason":"live_event_ingested","job_id":job_id,"snapshot":{"saved":True,"changed":bool(intake["snapshot"].get("changed")),"duplicate_content":bool(intake["snapshot"].get("duplicate_content"))},"context_ready":bool(intake.get("context_passport",{}).get("context_ready"))}
def process_event(*,url:str,event_id:str,source:str,text:str="",state_path:Path|None=None,snapshot_store:Path=DEFAULT_TEXT_SNAPSHOT_STORE)->dict[str,Any]:
    base=_base(source,url)
    if not event_id.strip() or source not in EVENT_SOURCES or not plan_discord_url_read(url).get("ok_to_open"): return {**base,"decision":"blocked","reason":"invalid_url_event"}
    key=_key(event_id.strip(),url,source); job_id=key[:20]; c=_connect(state_path or default_state_path()); now=time.time(); owner="direct-"+uuid.uuid4().hex
    c.execute("BEGIN IMMEDIATE"); row=c.execute("SELECT status,lease_expires_at FROM url_events WHERE event_key=?",(key,)).fetchone()
    if row and row[0]=="completed": c.execute("COMMIT"); c.close(); return {**base,"ok":True,"decision":"already_processed","reason":"duplicate_event","job_id":job_id}
    if row and row[0]=="processing" and float(row[1] or 0)>now: c.execute("COMMIT"); c.close(); return {**base,"ok":True,"decision":"already_queued","reason":"event_processing","job_id":job_id}
    if not row: c.execute("INSERT INTO url_events(event_key,job_id,event_source,target_url,status)VALUES(?,?,?,?,'pending')",(key,job_id,source,url))
    if text.strip(): c.execute("UPDATE url_events SET status='processing',owner=?,lease_expires_at=?,attempts=attempts+1 WHERE event_key=?",(owner,now+60,key))
    c.execute("COMMIT")
    if not text.strip(): c.close(); return {**base,"ok":True,"decision":"enqueued","reason":"live_refresh_job_accepted","job_id":job_id,"route_priority":["gateway_live_event","rest_backfill","bot_text_event_inbox","chrome_visible_fallback"],"chrome_event_role":"supplemental","reconciliation_required":True}
    payload=_complete(c,key=key,job_id=job_id,url=url,source=source,text=text,snapshot_store=snapshot_store,owner=owner); c.close(); return payload
def drain_once(*,state_path:Path,source_command:str,snapshot_store:Path=DEFAULT_TEXT_SNAPSHOT_STORE,timeout:float=30,lease_seconds:float=60)->dict[str,Any]:
    c=_connect(state_path); owner=uuid.uuid4().hex; now=time.time(); c.execute("BEGIN IMMEDIATE")
    row=c.execute("SELECT event_key,job_id,event_source,target_url FROM url_events WHERE status='pending' OR(status='processing' AND lease_expires_at<=?) ORDER BY rowid LIMIT 1",(now,)).fetchone()
    if not row: c.execute("COMMIT"); c.close(); return {**_base("worker",""),"ok":True,"decision":"idle","reason":"no_pending_job"}
    key,job_id,source,url=map(str,row); effective_lease=max(lease_seconds,timeout+60); c.execute("UPDATE url_events SET status='processing',owner=?,lease_expires_at=?,attempts=attempts+1 WHERE event_key=?",(owner,now+effective_lease,key)); c.execute("COMMIT")
    try:
        p=subprocess.run(split_secret_command(source_command),input=url,text=True,capture_output=True,timeout=timeout,shell=False,check=False); raw=p.stdout or ""
        if p.returncode or not raw.strip() or len(raw.encode())>MAX_INPUT_BYTES: raise ValueError("source_failed")
    except (OSError,subprocess.TimeoutExpired,ValueError):
        c.execute("UPDATE url_events SET status='pending',owner='',lease_expires_at=0 WHERE event_key=? AND owner=?",(key,owner)); c.close(); return {**_base(source,url),"decision":"blocked","reason":"live_source_failed","job_id":job_id}
    payload=_complete(c,key=key,job_id=job_id,url=url,source=source,text=raw,snapshot_store=snapshot_store,owner=owner); c.close(); return payload
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(); p.add_argument("--url"); p.add_argument("--event-id"); p.add_argument("--source",choices=sorted(EVENT_SOURCES)); p.add_argument("--input",type=Path); p.add_argument("--state",type=Path,default=default_state_path()); p.add_argument("--snapshot-store",type=Path,default=DEFAULT_TEXT_SNAPSHOT_STORE); p.add_argument("--drain-once",action="store_true"); p.add_argument("--source-command"); p.add_argument("--source-timeout",type=float,default=30); p.add_argument("--json",action="store_true"); a=p.parse_args(argv)
    if a.drain_once:
        if not a.source_command:p.error("--drain-once requires --source-command")
        payload=drain_once(state_path=a.state,source_command=a.source_command,snapshot_store=a.snapshot_store,timeout=a.source_timeout)
    else:
        if not a.url or not a.event_id or not a.source:p.error("event intake requires --url --event-id --source")
        text=""
        if a.input:
            try:
                if a.input.stat().st_size>MAX_INPUT_BYTES:raise ValueError()
                text=a.input.read_text(encoding="utf-8")
            except (OSError,UnicodeError,ValueError): payload={**_base(a.source,a.url),"decision":"blocked","reason":"private_input_unreadable_or_too_large"}; print(json.dumps(payload) if a.json else "decision: blocked"); return 2
        payload=process_event(url=a.url,event_id=a.event_id,source=a.source,text=text,state_path=a.state,snapshot_store=a.snapshot_store)
    print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True) if a.json else f"decision: {payload['decision']}\nreason: {payload['reason']}\noutbound_actions: disabled"); return 0 if payload["ok"] else 2
if __name__=="__main__": raise SystemExit(main())
