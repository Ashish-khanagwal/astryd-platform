"""Operator-only platform-admin provisioning; no default privileged credentials."""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--environment',choices=['staging','production'],default='staging')
    parser.add_argument('--email',required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    os.environ['ASTRYD_ENV']=args.environment
    from wsgi import app
    from app.extensions import mongo
    from app.platform.service import email,password,text,now
    from werkzeug.security import generate_password_hash
    address=email(args.email)
    name=text(args.name,'name')
    with app.app_context():
        if mongo.db.restaurant_users.find_one({'email':address}):
            raise SystemExit('Email already exists; existing account was not changed')
        if not args.apply:
            print(f'Dry-run: would create a platform administrator in {mongo.db.name}; no data changed')
        else:
            secret=password(getpass.getpass('New platform password: '))
            if secret!=getpass.getpass('Confirm password: '):raise SystemExit('Passwords do not match')
            mongo.db.restaurant_users.insert_one({'email':address,'name':name,'role':'super_admin','siteAccess':'all','restaurantId':None,'isActive':True,'emailVerified':True,'passwordHash':generate_password_hash(secret),'createdAt':now(),'updatedAt':now()})
            print('Platform administrator created; use /admin/superadmin/login')
