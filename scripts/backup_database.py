"""Snapshot BSON export, compatible with mongorestore; never writes to Atlas."""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bson import BSON, decode_all, json_util
from pymongo.read_concern import ReadConcern


def verify_backup(root):
    root=Path(root)
    manifest=json.loads((root/'manifest.json').read_text())
    for name,entry in manifest['collections'].items():
        data=(root/manifest['database']/(name+'.bson')).read_bytes()
        if hashlib.sha256(data).hexdigest()!=entry['sha256'] or len(decode_all(data))!=entry['count']:
            raise ValueError('Backup verification failed: '+name)
        json_util.loads((root/manifest['database']/(name+'.metadata.json')).read_text())
    return manifest


def backup(db, root):
    root=Path(root);root.mkdir(mode=0o700,parents=True,exist_ok=False)
    target=root/db.name;target.mkdir(mode=0o700)
    names=db.list_collection_names()
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+',n) for n in names):raise ValueError('Unsupported collection name')
    indexes={n:list(db[n].list_indexes()) for n in names}
    manifest={'database':db.name,'createdAt':datetime.now(timezone.utc).isoformat(),'collections':{}}
    with db.client.start_session() as session:
        session.start_transaction(read_concern=ReadConcern('snapshot'))
        try:
            for name in names:
                docs=list(db[name].find({},session=session))
                data=b''.join(BSON.encode(d) for d in docs)
                (target/(name+'.bson')).write_bytes(data)
                (target/(name+'.metadata.json')).write_text(json_util.dumps({'indexes':indexes[name],'options':{}}))
                manifest['collections'][name]={'count':len(docs),'sha256':hashlib.sha256(data).hexdigest()}
        finally:session.abort_transaction()
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    verify_backup(root)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--environment',choices=['staging','production'],required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();os.environ['ASTRYD_ENV']=args.environment;os.environ['MONGO_CREATE_INDEXES']='false'
    from wsgi import app
    from app.extensions import mongo
    with app.app_context():
        manifest=backup(mongo.db,args.output)
        print(f"Verified snapshot backup: {manifest['database']}; {len(manifest['collections'])} collections; {args.output}")
