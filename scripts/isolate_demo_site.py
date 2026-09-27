"""Explicit staging-only repair; preserves all site IDs and transaction/content data."""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.seed_multitenant import stable_id


def isolate(db):
    if db.name != 'astryd_staging':
        raise ValueError('This repair is restricted to staging')
    sid = 'rest_lumiere_nh'
    target = str(stable_id('org_lumiere_nh'))
    source = str(stable_id('org_lumiere'))
    owner = str(stable_id('user_lumiere_nh_owner'))
    def repair(session):
        site = db.restaurants.find_one({'restaurantId': sid}, session=session)
        if not site or site['organizationId'] not in {source, target}:
            raise ValueError('Unexpected demo Site ownership; refusing to move it')
        if not db.organizations.find_one({'_id': stable_id('org_lumiere_nh'), 'code': 'LUMIERENH'}, session=session):
            raise ValueError('Run the staging seed first to provision the separate demo account')
        db.restaurants.update_one({'restaurantId': sid}, {'$set': {'organizationId': target, 'ownerUserId': owner}}, session=session)
        db.businesses.update_one({'business_id': sid}, {'$set': {'organizationId': target}}, session=session)
        db.restaurant_users.update_one({'_id': stable_id('user_lumiere_nh_owner'), 'organizationId': target}, {'$set': {'restaurantId': sid}}, session=session)
        db.restaurant_users.update_many({'organizationId': source, 'siteAccess': sid}, {'$pull': {'siteAccess': sid}}, session=session)
    with db.client.start_session() as session:
        session.with_transaction(repair)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print('DRY RUN: separate only staging rest_lumiere_nh into LUMIERENH; preserve content and payments.')
    else:
        os.environ['ASTRYD_ENV'] = 'staging'
        from wsgi import app
        from app.extensions import mongo
        with app.app_context():
            isolate(mongo.db)
        print('Staging demo Site isolated; production unchanged.')
