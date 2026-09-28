"""Idempotent additive migration. Defaults to dry-run; never changes payment data."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bson import ObjectId


def migrate(db, apply=False):
    from app.platform.service import DEFAULTS, MODULES, PERMISSIONS, TEMPLATE_VARIANTS, now
    sites = list(db.restaurants.find({}))
    site_ids=set()
    for site in sites:
        sid=site.get('restaurantId',site.get('business_id'))
        if not sid or sid in site_ids:raise ValueError('Missing or duplicate canonical Site ID')
        site_ids.add(sid)
        if site.get('vertical','restaurant') not in DEFAULTS:raise ValueError('Unknown Site vertical')
        org_id=site.get('organizationId')
        if org_id and not db.organizations.find_one({'_id':ObjectId(org_id)}):raise ValueError('Site references a missing organization')
    for user in db.restaurant_users.find({'role':{'$ne':'super_admin'}}):
        if user.get('restaurantId') not in site_ids:raise ValueError('User references an unknown Site; resolve before migration')
    # Check normalized collisions before changing any records or indexes.
    seen=set()
    for user in db.restaurant_users.find({}):
        scope=user.get('organizationId') or user.get('restaurantId') or 'platform'
        key=(scope,user.get('email','').strip().lower())
        if key in seen:raise ValueError('Duplicate normalized email within a tenant; resolve before migration')
        seen.add(key)
    changes = []
    for site in sites:
        sid = site.get('restaurantId', site.get('business_id'))
        if not sid:
            raise ValueError('Site is missing its canonical ID; resolve before migration')
        if site.get('organizationId'):
            org_id = site['organizationId']
        else:
            code = 'LUMIERE' if sid == 'lumiere-mayfair' else 'ORG' + str(site['_id'])[-8:].upper()
            existing = db.organizations.find_one({'code': code})
            org_id = str(existing['_id']) if existing else str(ObjectId())
            changes.append(f'Backfill organization for {sid} ({code})')
            if apply:
                db.organizations.update_one({'code': code}, {'$setOnInsert': {'_id': ObjectId(org_id), 'code': code, 'name': site.get('name', 'Lumière' if code == 'LUMIERE' else code), 'status': 'active', 'createdAt': now()}}, upsert=True)
                db.restaurants.update_one({'_id': site['_id']}, {'$set': {'organizationId': org_id, 'vertical': site.get('vertical', 'restaurant'), 'brandingBadgeEnabled': site.get('brandingBadgeEnabled', True)}})
        vertical = site.get('vertical', 'restaurant')
        changes.append(f'Ensure four module defaults and existing-user access for {sid}')
        if apply:
            db.businesses.update_one({'business_id': sid}, {'$set': {'organizationId': org_id}})
            for user in db.restaurant_users.find({'restaurantId': sid}):
                role = 'owner' if user.get('role') in {'owner', 'admin'} else user.get('role', 'staff')
                values = {'organizationId': org_id, 'email': user['email'].strip().lower(), 'normalizedEmail': user['email'].strip().lower(), 'role': role,
                          'siteAccess': user.get('siteAccess', 'all' if role == 'owner' else [sid]), 'emailVerified': user.get('emailVerified', False),
                          'permissions': {**{p: role == 'owner' or p in {'menu','booking'} for p in PERMISSIONS}, **user.get('permissions', {})}}
                db.restaurant_users.update_one({'_id': user['_id']}, {'$set': values})
            for order, (module, (label, enabled)) in enumerate(zip(MODULES, DEFAULTS[vertical])):
                # Legacy restaurant navigation exposed every module before page configs existed.
                # Keep that behavior during migration; new-account defaults stay unchanged.
                if vertical == 'restaurant': enabled = True
                default = {'module': module, 'navLabel': label, 'enabled': enabled, 'order': order, 'templateVariant': TEMPLATE_VARIANTS[vertical][order]}
                db.page_configs.update_one({'restaurantId': sid, 'module': module}, {'$setOnInsert': {'restaurantId': sid, **default, 'published': default}}, upsert=True)
                existing_page=db.page_configs.find_one({'restaurantId':sid,'module':module})
                if 'published' not in existing_page:
                    snapshot={k:existing_page.get(k,default[k]) for k in default}
                    db.page_configs.update_one({'_id':existing_page['_id']},{'$set':{'published':snapshot}})
            for section in db.homepage_sections.find({'restaurantId': sid}):
                db.homepage_sections.update_one({'_id': section['_id']}, {'$set': {'publishedVisible':section.get('publishedVisible',section.get('visible',True)), 'publishedOrder':section.get('publishedOrder',section.get('order',0))}})
            db.page_content.update_one({'restaurantId':sid},{'$setOnInsert':{'restaurantId':sid,'draft':{},'published':{},'updatedAt':now()}},upsert=True)
    if apply:
        # Replace only the global email constraint, after all users have been scoped.
        remaining = db.restaurant_users.count_documents({'organizationId': {'$exists': False}, 'role': {'$ne': 'super_admin'}})
        if remaining:
            raise ValueError('Unscoped users remain; global email index was retained')
        from app.database.indexes import initialize_indexes
        initialize_indexes(db)
        for name, info in db.restaurant_users.index_information().items():
            if info.get('key') == [('email', 1)] and info.get('unique'):
                db.restaurant_users.drop_index(name)
    return changes


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--environment', choices=['staging','production'], default='staging')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir')
    parser.add_argument('--maintenance-confirmed',action='store_true')
    args = parser.parse_args()
    import os
    os.environ['ASTRYD_ENV'] = args.environment
    os.environ['MONGO_CREATE_INDEXES'] = 'false'
    from wsgi import app
    from app.extensions import mongo
    with app.app_context():
        if args.apply and args.environment=='production':
            if not args.backup_dir or not args.maintenance_confirmed:raise ValueError('Production apply requires --backup-dir and --maintenance-confirmed')
            from scripts.backup_database import verify_backup
            if verify_backup(args.backup_dir)['database']!=mongo.db.name:raise ValueError('Backup targets a different database')
        print(f'Database: {mongo.db.name}; mode: {"APPLY" if args.apply else "DRY RUN"}')
        for change in migrate(mongo.db, args.apply):
            print(change)
