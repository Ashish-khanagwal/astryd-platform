"""Seed the frontend demo content into an isolated staging database, never production."""
import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bson import ObjectId
from werkzeug.security import generate_password_hash


def stable_id(value):
    return ObjectId(hashlib.sha256(value.encode()).hexdigest()[:24])


def seed(db, fixture):
    from app.platform.service import PERMISSIONS, now
    if db.name != 'astryd_staging':
        raise ValueError('Demo seeding is restricted to astryd_staging')
    fixture = copy.deepcopy(fixture)
    secondary = next(s for s in fixture['restaurants'] if s['id'] == 'rest_lumiere_nh')
    secondary.update(organizationId='org_lumiere_nh', ownerUserId='user_lumiere_nh_owner')
    organization = copy.deepcopy(next(o for o in fixture['organizations'] if o['id'] == 'org_lumiere'))
    organization.update(id='org_lumiere_nh', code='LUMIERENH', name='Lumière – Notting Hill')
    fixture['organizations'].append(organization)
    owner = copy.deepcopy(next(u for u in fixture['users'] if u['id'] == 'user_owner'))
    owner.update(id='user_lumiere_nh_owner', organizationId='org_lumiere_nh', restaurantId='rest_lumiere_nh', email='owner@lumierenottinghill.com')
    fixture['users'].append(owner)
    site_map = {'rest_lumiere': 'lumiere-mayfair'}
    # Stable reference conversion keeps categories/media/addons/sections consistent across reruns.
    identifiers = set()
    for key in ('users','organizations','media','categories','items','addons','offers','membershipPlans','members','memberCheckIns'):
        identifiers.update(d['id'] for d in fixture.get(key, []))
    for versions in fixture['homepage'].values():
        for homepage in versions.values():
            identifiers.update(s['id'] for s in homepage['sections'])
    def convert(value):
        if isinstance(value, str):
            if value in site_map:
                return site_map[value]
            if value in identifiers:
                return str(stable_id(value))
            return value
        if isinstance(value,list):
            return [convert(v) for v in value]
        if isinstance(value,dict):
            return {k:convert(v) for k,v in value.items()}
        return value
    def doc(value):
        value = convert(value)
        if 'id' in value:
            value['_id'] = ObjectId(value.pop('id'))
        for field in ('createdAt','updatedAt','checkedInAt','publishedAt'):
            if isinstance(value.get(field),str):
                value[field] = datetime.fromisoformat(value[field].replace('Z','+00:00'))
        return value
    def insert(collection, query, value):
        db[collection].update_one(query, {'$setOnInsert':value}, upsert=True)
    for raw in fixture['organizations']:
        value = doc(raw); value['status'] = 'active'
        insert('organizations',{'code':value['code']},value)
    for raw in fixture['restaurants']:
        value = convert(raw); sid = site_map.get(raw['id'],raw['id']);value.pop('id');value.update(restaurantId=sid,business_id=sid)
        value['_id']=stable_id('restaurant:'+sid)
        insert('restaurants',{'restaurantId':sid},value)
    for raw in fixture['users']:
        if raw['role']=='super_admin':
            continue  # Never create a platform administrator with a demo password.
        value=doc(raw)
        if raw['role']=='owner':
            value['email']=raw['email'].replace('owner@','admin@')
        value.update(passwordHash=generate_password_hash('admin123'), normalizedEmail=value['email'].lower(), emailVerified=True,
                     permissions={p:raw['role']=='owner' or p in {'menu','booking'} for p in PERMISSIONS})
        insert('restaurant_users',{'organizationId':value['organizationId'],'email':value['email']},value)
    for key,collection in [('media','media_assets'),('categories','menu_categories'),('items','menu_items'),('addons','addons'),('offers','offers'),('membershipPlans','membership_plans'),('members','members'),('memberCheckIns','member_check_ins')]:
        for raw in fixture.get(key,[]):
            value=doc(raw);insert(collection,{'_id':value['_id']},value)
    for original,versions in fixture['homepage'].items():
        sid=site_map.get(original,original)
        published={s['type']:s for s in versions['published']['sections']}
        for raw in versions['draft']['sections']:
            value=doc(raw);live=convert(published[raw['type']]);value['draftContent']=value.pop('content');value.update(publishedContent=live['content'],publishedOrder=live['order'],publishedVisible=live['visible'])
            insert('homepage_sections',{'restaurantId':sid,'type':value['type']},value)
    for original,versions in fixture['brand'].items():
        sid=site_map.get(original,original);insert('brand_settings',{'restaurantId':sid},{'restaurantId':sid,**convert(versions)})
    for original,value in fixture['website'].items():
        sid=site_map.get(original,original);insert('website_settings',{'restaurantId':sid},convert(value))
    for raw in fixture['pageConfigs']:
        value=convert(raw);value.pop('id',None);value['published']={k:value[k] for k in ('enabled','navLabel','order','templateVariant')}
        insert('page_configs',{'restaurantId':value['restaurantId'],'module':value['module']},value)
    for original,value in fixture['pageContent'].items():
        sid=site_map.get(original,original);insert('page_content',{'restaurantId':sid},{'restaurantId':sid,**convert(value)})
    weekdays=['mon','tue','wed','thu','fri','sat','sun']
    for original,settings in fixture['reservationAvailability'].items():
        sid=site_map.get(original,original)
        site=db.restaurants.find_one({'restaurantId':sid})
        hours=[]
        for day in settings['days']:
            hours.append({'day_of_week':weekdays.index(day['day']),'open_time':day['openTime'],'close_time':day['closeTime'],'slot_duration_mins':day['slotDurationMins'],'max_per_slot':day['maxPerSlot'],'is_closed':day['isClosed'],'disabled_slots':[s['time'] for s in day.get('slots',[]) if not s['isOpen']]})
        insert('businesses',{'business_id':sid},{'business_id':sid,'organizationId':site['organizationId'],'name':site['name'],'type':site['vertical'],'operating_hours':hours,'blocked_dates':settings.get('blockedDates',[]),'created_at':now()})
        for seating in ('Indoor','Outdoor','The Bar','Private'):
            for index in range(12):
                resource_id=stable_id(f'resource:{sid}:{seating}:{index}')
                insert('tables',{'_id':resource_id},{'_id':resource_id,'business_id':sid,'seating_type':seating,'capacity':100,'is_active':True,'name':f'Resource {index+1}'})
    from app.database.indexes import initialize_indexes
    initialize_indexes(db)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    fixture=json.loads(Path(__file__).with_name('demo_data.json').read_text())
    if not args.apply:
        print('DRY RUN: isolated staging only; six sites, six independent organizations; existing content is not overwritten. Use --apply to seed.')
    else:
        os.environ['ASTRYD_ENV']='staging'
        from wsgi import app
        from app.extensions import mongo
        with app.app_context():
            seed(mongo.db,fixture)
            print('Staging demo content seeded; production untouched.')
