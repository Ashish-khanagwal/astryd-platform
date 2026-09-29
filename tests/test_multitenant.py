"""Tenant contract/regression tests without Atlas or payment network calls.

The transaction adapter tests rollback semantics, not MongoDB concurrency; Atlas
replica-set transactions must also be verified during staged deployment.
"""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import mongomock
from bson import ObjectId
from app import create_app
from app.extensions import mongo
from scripts.seed_multitenant import seed
from scripts.migrate_multitenant import migrate


class Collection:
    def __init__(self, raw): self.raw=raw
    def __getattr__(self,name):
        fn=getattr(self.raw,name)
        def run(*args,**kwargs):
            kwargs.pop('session',None)
            return fn(*args,**kwargs)
        return run


class Session:
    def __init__(self,db): self.db=db
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def with_transaction(self,fn):
        snapshot={c:copy.deepcopy(list(self.db[c].find({}))) for c in self.db.list_collection_names()}
        try: return fn(self)
        except Exception:
            for c in self.db.list_collection_names(): self.db[c].delete_many({})
            for c,docs in snapshot.items():
                if docs:self.db[c].insert_many(docs)
            raise


class Database:
    def __init__(self,raw): self.raw=raw; self.client=self
    def __getitem__(self,key): return Collection(self.raw[key])
    def __getattr__(self,key): return self[key]
    def start_session(self): return Session(self.raw)


class TenantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads((Path(__file__).parents[1]/'scripts/demo_data.json').read_text())
        cls.app=create_app('testing')

    def setUp(self):
        self.raw=mongomock.MongoClient(tz_aware=True).astryd_staging
        seed(self.raw,self.fixture)
        self.db=Database(self.raw)
        self.patch=patch.object(mongo,'db',self.db);self.patch.start();self.addCleanup(self.patch.stop)
        self.client=self.app.test_client()

    def login(self,code='LUMIERE',address='admin@lumiere.com'):
        r=self.client.post('/api/v1/auth/login',json={'orgId':code,'email':address,'password':'admin123'})
        self.assertEqual(r.status_code,200,r.json)
        return {'Authorization':'Bearer '+r.json['token']},r.json['user']

    def test_all_demo_logins_are_real_and_scoped(self):
        for code,address in [('LUMIERE','admin@lumiere.com'),('PULSEFIT','admin@pulsefit.com'),('NOVAGOODS','admin@novagoods.com')]:
            headers,user=self.login(code,address)
            r=self.client.get('/api/v1/organizations/'+user['organizationId']+'/sites',headers=headers)
            self.assertEqual(r.status_code,200)
            self.assertTrue(all(s['organizationId']==user['organizationId'] for s in r.json))
            self.assertNotIn('passwordHash',user)

    def test_wrong_organization_login_denied(self):
        r=self.client.post('/api/v1/auth/login',json={'orgId':'PULSEFIT','email':'admin@lumiere.com','password':'admin123'})
        self.assertEqual(r.status_code,401)

    def test_separate_demo_location_is_isolated(self):
        h,u=self.login()
        sites=self.client.get('/api/v1/organizations/'+u['organizationId']+'/sites',headers=h).json
        self.assertEqual([s['id'] for s in sites],['lumiere-mayfair'])
        self.assertEqual(self.client.get('/api/v1/restaurants/rest_lumiere_nh/brand?version=draft',headers=h).status_code,403)
        h,u=self.login('LUMIERENH','admin@lumierenottinghill.com')
        self.assertEqual(self.client.get('/api/v1/restaurants/rest_lumiere_nh/brand?version=draft',headers=h).status_code,200)

    def test_cross_tenant_content_and_bookings_denied(self):
        h,_=self.login()
        for url in ['/restaurants/site_pulsefit/page-content?version=draft','/admin/reservations?siteId=site_pulsefit','/admin/reservation-availability?siteId=site_pulsefit','/sites/site_pulsefit/membership/members']:
            self.assertEqual(self.client.get('/api/v1'+url,headers=h).status_code,403,url)

    def test_cross_tenant_org_list_denied(self):
        h,_=self.login();org=self.raw.organizations.find_one({'code':'PULSEFIT'})
        self.assertEqual(self.client.get('/api/v1/organizations/'+str(org['_id'])+'/sites',headers=h).status_code,403)

    def test_live_user_deactivation_invalidates_existing_token(self):
        h,u=self.login();self.raw.restaurant_users.update_one({'_id':ObjectId(u['id'])},{'$set':{'isActive':False}})
        self.assertEqual(self.client.get('/api/v1/restaurants/lumiere-mayfair/users',headers=h).status_code,403)

    def test_staff_site_access_change_is_immediate(self):
        h,u=self.login('LUMIERE','staff@lumiere.com')
        self.raw.restaurant_users.update_one({'_id':ObjectId(u['id'])},{'$set':{'siteAccess':[]}})
        self.assertEqual(self.client.get('/api/v1/admin/reservations',headers=h).status_code,403)

    def test_owner_cannot_create_owner_or_cross_org_staff(self):
        h,_=self.login()
        base={'name':'Staff','email':'test@example.com','password':'StrongPass123','siteAccess':['lumiere-mayfair']}
        self.assertEqual(self.client.post('/api/v1/restaurants/lumiere-mayfair/users',headers=h,json={**base,'role':'owner'}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/restaurants/lumiere-mayfair/users',headers=h,json={**base,'siteAccess':['site_pulsefit']}).status_code,400)

    def test_staff_permission_denied(self):
        h,_=self.login('LUMIERE','staff@lumiere.com')
        self.assertEqual(self.client.put('/api/v1/restaurants/lumiere-mayfair/brand',headers=h,json={'tagline':'Bad'}).status_code,403)

    def test_preview_is_private_and_public_resolver_is_dynamic(self):
        self.assertEqual(self.client.get('/api/v1/restaurants/site_pulsefit/homepage?version=draft').status_code,401)
        r=self.client.get('/api/v1/public/sites/resolve?slug=pulsefit-downtown')
        self.assertEqual(r.status_code,200);self.assertEqual(r.json['id'],'site_pulsefit')
        before=self.raw.homepage_sections.count_documents({})
        self.assertEqual(self.client.get('/api/v1/restaurants/missing/homepage').status_code,404)
        self.assertEqual(before,self.raw.homepage_sections.count_documents({}))

    def test_page_drafts_only_become_live_on_publish(self):
        h,_=self.login()
        self.assertEqual(self.client.patch('/api/v1/sites/lumiere-mayfair/pages/catalog',headers=h,json={'navLabel':'New Store'}).status_code,200)
        live=self.client.get('/api/v1/sites/lumiere-mayfair/pages').json
        self.assertNotEqual(next(x for x in live if x['module']=='catalog')['navLabel'],'New Store')
        self.assertEqual(self.client.post('/api/v1/restaurants/lumiere-mayfair/publish',headers=h).status_code,200)
        live=self.client.get('/api/v1/sites/lumiere-mayfair/pages').json
        self.assertEqual(next(x for x in live if x['module']=='catalog')['navLabel'],'New Store')

    def test_manual_membership_is_private_and_site_scoped(self):
        h,_=self.login('PULSEFIT','admin@pulsefit.com')
        plan=self.client.post('/api/v1/sites/site_pulsefit/membership/plans',headers=h,json={'name':'Trial','priceCents':1000}).json
        payload={'planId':plan['id'],'customerName':'Guest','customerEmail':'guest@example.com','status':'active','notes':'forged'}
        r=self.client.post('/api/v1/sites/site_pulsefit/membership/members',json=payload)
        self.assertEqual(r.status_code,402,r.json)
        payload['customerPhone']='5551234567'
        r=self.client.post('/api/v1/sites/site_pulsefit/membership/checkout',headers=h,json=payload)
        self.assertEqual(r.status_code,201,r.json)
        member=self.raw.members.find_one({'customerEmail':'guest@example.com'})
        self.assertEqual(member['status'],'paused');self.assertNotIn('notes',member)
        self.assertEqual(self.client.get('/api/v1/sites/site_pulsefit/membership/members?email=guest@example.com').status_code,401)
        self.assertEqual(self.client.post('/api/v1/sites/site_pulsefit/membership/members/'+str(member['_id'])+'/check-ins',headers=h).status_code,409)
        self.assertEqual(self.client.post('/api/v1/sites/site_novagoods/membership/checkout',json=payload).status_code,400)
        self.assertEqual(self.client.patch('/api/v1/sites/site_pulsefit/membership/members/'+str(member['_id']),headers=h,json={'status':'active'}).status_code,409)
        from app.payments.service import apply_transfer_state,create_retry
        pid=member['paymentId']
        apply_transfer_state(self.db,pid,{'id':'TR_TEST_FAILED','state':'FAILED'})
        self.assertEqual(self.raw.members.find_one({'_id':member['_id']})['status'],'paused')
        payment,secret=create_retry(self.db,str(pid),r.json['checkout_secret'])
        apply_transfer_state(self.db,payment['_id'],{'id':'TR_TEST_SUCCEEDED','state':'SUCCEEDED'})
        apply_transfer_state(self.db,payment['_id'],{'id':'TR_TEST_SUCCEEDED','state':'SUCCEEDED'})
        self.assertEqual(self.raw.members.find_one({'_id':member['_id']})['status'],'active')
        self.assertEqual(self.raw.payments.find_one({'_id':payment['_id']})['fulfillment_status'],'completed')

    def test_logout_invalidates_token(self):
        h,_=self.login();self.assertEqual(self.client.post('/api/v1/auth/logout',headers=h).status_code,200)
        self.assertEqual(self.client.get('/api/v1/auth/me',headers=h).status_code,404)

    def test_atomic_signup_defaults_and_rollback(self):
        body={'organizationName':'New Gym','siteName':'New Gym','vertical':'gym','slug':'new-gym','ownerName':'Owner','ownerEmail':'owner@example.com','password':'StrongPass123','passwordConfirmation':'StrongPass123','branding':{'themePresetId':'emerald'}}
        with patch('app.auth.routes.issue_link'):
            r=self.client.post('/api/v1/auth/signup',json=body)
        self.assertEqual(r.status_code,201,r.json);self.assertEqual(r.json['orgCode'],'NEWGYM')
        sid=r.json['user']['restaurantId'];self.assertEqual(self.raw.page_configs.count_documents({'restaurantId':sid}),4)
        before=self.raw.organizations.count_documents({})
        with patch('app.platform.service.insert_site',side_effect=ValueError('Provision failed')):
            r=self.client.post('/api/v1/auth/signup',json={**body,'slug':'another'})
        self.assertEqual(r.status_code,400);self.assertEqual(before,self.raw.organizations.count_documents({}))

    def test_salon_and_coffee_signup_preserves_module_layouts_and_isolation(self):
        from app.platform.service import DEFAULTS, TEMPLATE_VARIANTS, MODULES
        previous = None
        for vertical in ('salon', 'coffee'):
            slug = 'new-' + vertical
            modules = [dict(module=module, navLabel=DEFAULTS[vertical][n][0],
                            enabled=DEFAULTS[vertical][n][1], templateVariant=TEMPLATE_VARIANTS[vertical][n])
                       for n, module in enumerate(MODULES)]
            if vertical == 'coffee':
                modules[1]['templateVariant'] = 'b'
            body = {'organizationName': slug, 'siteName': slug, 'vertical': vertical,
                    'slug': slug, 'ownerName': 'Owner', 'ownerEmail': slug+'@example.com',
                    'password': 'StrongPass123', 'passwordConfirmation': 'StrongPass123', 'modules': modules}
            with patch.dict(self.app.config, REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH=False):
                response = self.client.post('/api/v1/auth/signup', json=body)
            self.assertEqual(response.status_code, 201, response.json)
            sid = response.json['user']['restaurantId']
            headers = {'Authorization': 'Bearer '+response.json['token']}
            self.assertEqual(self.client.post('/api/v1/auth/login', json={
                'orgId': response.json['orgCode'], 'email': body['ownerEmail'], 'password': body['password']}).status_code, 200)
            pages = self.client.get(f'/api/v1/sites/{sid}/pages?version=draft', headers=headers).json
            self.assertEqual([(p['module'], p['templateVariant']) for p in pages],
                             [(m['module'], m['templateVariant']) for m in modules])
            if previous:
                self.assertEqual(self.client.get(f'/api/v1/restaurants/{previous}/brand?version=draft', headers=headers).status_code, 403)
            previous = sid
        bad = {**body, 'slug': 'bad-variant', 'modules': [{**m, 'templateVariant': 'z'} for m in modules]}
        self.assertEqual(self.client.post('/api/v1/auth/signup', json=bad).status_code, 400)
        bad['modules'][0]['templateVariant'] = {'invalid': True}
        self.assertEqual(self.client.post('/api/v1/auth/signup', json=bad).status_code, 400)
        defaults = {**body, 'slug': 'coffee-defaults', 'organizationName': 'Coffee defaults',
                    'ownerEmail': 'coffee-defaults@example.com'}
        defaults.pop('modules')
        with patch.dict(self.app.config, REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH=False):
            response = self.client.post('/api/v1/auth/signup', json=defaults)
        self.assertEqual(response.status_code, 201, response.json)
        sid = response.json['user']['restaurantId']
        self.assertEqual([p['templateVariant'] for p in self.client.get(f'/api/v1/sites/{sid}/pages').json],
                         list(TEMPLATE_VARIANTS['coffee']))
        migrate(self.raw, True)
        saved = list(self.raw.page_configs.find({'restaurantId': previous}).sort('order', 1))
        self.assertEqual([p['templateVariant'] for p in saved], [m['templateVariant'] for m in modules])

    def test_homepage_layout_draft_publish_and_invalid_variant(self):
        headers, _ = self.login()
        url = '/api/v1/restaurants/lumiere-mayfair/homepage'
        before = next(s for s in self.client.get(url).json['sections'] if s['type'] == 'hero')
        self.assertEqual(before['templateVariant'], 'a')
        self.assertEqual(self.client.put(url+'/sections/hero', headers=headers, json={'templateVariant':'z'}).status_code, 400)
        self.assertEqual(self.client.put(url+'/sections/hero', headers=headers, json={'templateVariant':{}}).status_code, 400)
        self.assertEqual(self.client.put(url+'/sections/hero', headers=headers, json={'templateVariant':'c'}).status_code, 200)
        brand_url = '/api/v1/restaurants/lumiere-mayfair/brand'
        self.assertEqual(self.client.put(brand_url, headers=headers, json={'headerVariant':'b','footerVariant':'c','showCart':False}).status_code, 200)
        self.assertNotEqual(self.client.get(brand_url).json.get('headerVariant'), 'b')
        draft = next(s for s in self.client.get(url+'?version=draft', headers=headers).json['sections'] if s['type'] == 'hero')
        live = next(s for s in self.client.get(url).json['sections'] if s['type'] == 'hero')
        self.assertEqual(draft['templateVariant'], 'c')
        self.assertEqual(live['templateVariant'], 'a')
        self.assertEqual(self.client.post('/api/v1/restaurants/lumiere-mayfair/publish', headers=headers).status_code, 200)
        live = next(s for s in self.client.get(url).json['sections'] if s['type'] == 'hero')
        self.assertEqual(live['templateVariant'], 'c')
        self.assertNotIn('publishedTemplateVariant', live)
        brand = self.client.get(brand_url).json
        self.assertEqual((brand['headerVariant'],brand['footerVariant'],brand['showCart']),('b','c',False))
    def test_salon_signup_uses_per_module_layouts(self):
        body={'organizationName':'Glow Salon','siteName':'Glow Salon','vertical':'salon','slug':'glow-salon','ownerName':'Owner','ownerEmail':'glow@example.com','password':'StrongPass123','passwordConfirmation':'StrongPass123','branding':{'themePresetId':'rosewood'}}
        with patch('app.auth.routes.issue_link'):
            r=self.client.post('/api/v1/auth/signup',json=body)
        self.assertEqual(r.status_code,201,r.json)
        sid=r.json['user']['restaurantId']
        pages={p['module']:p for p in self.raw.page_configs.find({'restaurantId':sid})}
        self.assertEqual({m:(p['navLabel'],p['enabled'],p['templateVariant']) for m,p in pages.items()},
                         {'items':('Services',True,'a'),'catalog':('Shop',False,'a'),'booking':('Book Now',True,'c'),'membership':('Memberships',True,'c')})
        self.assertEqual(self.raw.brand_settings.find_one({'restaurantId':sid})['draft']['themePresetId'],'rosewood')

    def test_coffee_signup_uses_per_module_layouts(self):
        body={'organizationName':'Daily Grind','siteName':'Daily Grind','vertical':'coffee','slug':'daily-grind','ownerName':'Owner','ownerEmail':'grind@example.com','password':'StrongPass123','passwordConfirmation':'StrongPass123','branding':{'themePresetId':'espresso'}}
        with patch('app.auth.routes.issue_link'):
            r=self.client.post('/api/v1/auth/signup',json=body)
        self.assertEqual(r.status_code,201,r.json)
        sid=r.json['user']['restaurantId']
        pages={p['module']:p for p in self.raw.page_configs.find({'restaurantId':sid})}
        self.assertEqual({m:(p['navLabel'],p['enabled'],p['templateVariant']) for m,p in pages.items()},
                         {'items':('Menu',True,'a'),'catalog':('Order Ahead',True,'a'),'booking':('Reserve a Table',False,'a'),'membership':('Rewards',True,'a')})
        self.assertEqual(self.raw.brand_settings.find_one({'restaurantId':sid})['draft']['themePresetId'],'espresso')

    def test_laundry_signup_uses_per_module_layouts(self):
        body={'organizationName':'Brightside Two','siteName':'Brightside Two','vertical':'laundry','slug':'brightside-two','ownerName':'Owner','ownerEmail':'brightside2@example.com','password':'StrongPass123','passwordConfirmation':'StrongPass123','branding':{'themePresetId':'aqua'}}
        with patch('app.auth.routes.issue_link'):
            r=self.client.post('/api/v1/auth/signup',json=body)
        self.assertEqual(r.status_code,201,r.json)
        sid=r.json['user']['restaurantId']
        pages={p['module']:p for p in self.raw.page_configs.find({'restaurantId':sid})}
        self.assertEqual({m:(p['navLabel'],p['enabled'],p['templateVariant']) for m,p in pages.items()},
                         {'items':('Services',True,'a'),'catalog':('Order Online',True,'a'),'booking':('Schedule Pickup',True,'a'),'membership':('Laundry Plan',True,'a')})
        self.assertEqual(self.raw.brand_settings.find_one({'restaurantId':sid})['draft']['themePresetId'],'aqua')

    def test_new_site_defaults_to_layout_a_everywhere_and_can_switch(self):
        body={'organizationName':'Layout Co','siteName':'Layout Co','vertical':'restaurant','slug':'layout-co','ownerName':'Owner','ownerEmail':'layout@example.com','password':'StrongPass123','passwordConfirmation':'StrongPass123'}
        with patch('app.auth.routes.issue_link'):
            r=self.client.post('/api/v1/auth/signup',json=body)
        self.assertEqual(r.status_code,201,r.json)
        sid=r.json['user']['restaurantId'];h={'Authorization':'Bearer '+r.json['token']}
        brand=self.client.get('/api/v1/restaurants/'+sid+'/brand?version=draft',headers=h).json
        self.assertEqual((brand['headerVariant'],brand['footerVariant']),('a','a'))
        self.assertTrue(brand['showCart'])
        sections=self.client.get('/api/v1/restaurants/'+sid+'/homepage?version=draft',headers=h).json['sections']
        self.assertTrue(sections);self.assertTrue(all(s['templateVariant']=='a' for s in sections))
        r=self.client.put('/api/v1/restaurants/'+sid+'/homepage/sections/hero',headers=h,json={'templateVariant':'b'})
        self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['templateVariant'],'b')
        self.assertEqual(self.client.get('/api/v1/restaurants/'+sid+'/homepage?version=published').json['sections'][0]['templateVariant'],'a')
        with patch.dict(self.app.config,REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH=False):
            self.assertEqual(self.client.post('/api/v1/restaurants/'+sid+'/publish',headers=h).status_code,200)
        published=[s for s in self.client.get('/api/v1/restaurants/'+sid+'/homepage?version=published').json['sections'] if s['type']=='hero'][0]
        self.assertEqual(published['templateVariant'],'b')
        self.assertEqual(self.client.put('/api/v1/restaurants/'+sid+'/homepage/sections/hero',headers=h,json={'templateVariant':'z'}).status_code,400)

    def test_deferred_verification_publishes_without_faking_verification(self):
        body={'organizationName':'Deferred Gym','siteName':'Deferred Gym','vertical':'gym','slug':'deferred-gym','ownerName':'Owner','ownerEmail':'deferred@example.com','password':'StrongPass123','passwordConfirmation':'StrongPass123'}
        with patch.dict(self.app.config,REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH=False), patch('app.auth.routes.issue_link') as link:
            r=self.client.post('/api/v1/auth/signup',json=body)
            self.assertEqual(r.status_code,201,r.json);link.assert_not_called()
            self.assertFalse(r.json['emailVerificationRequiredForPublish'])
            self.assertFalse(r.json['user']['emailVerified'])
            sid=r.json['user']['restaurantId'];h={'Authorization':'Bearer '+r.json['token']}
            self.assertEqual(self.client.get('/api/v1/public/sites/resolve?slug=deferred-gym').status_code,404)
            self.assertEqual(self.client.post('/api/v1/restaurants/'+sid+'/publish',headers=h).status_code,200)
            website=self.raw.website_settings.find_one({'restaurantId':sid})
            self.assertEqual(website['publishStatus'],'published');self.assertIsNotNone(website['publishedAt'])
            self.assertEqual(self.client.get('/api/v1/public/sites/resolve?slug=deferred-gym').status_code,200)
            self.client.put('/api/v1/restaurants/'+sid+'/brand',headers=h,json={'tagline':'Draft only'})
            self.assertNotEqual(self.client.get('/api/v1/restaurants/'+sid+'/brand').json.get('tagline'),'Draft only')
        with patch.dict(self.app.config,REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH=True):
            self.assertEqual(self.client.post('/api/v1/restaurants/'+sid+'/publish',headers=h).status_code,403)
        self.assertFalse(self.raw.restaurant_users.find_one({'email':'deferred@example.com'})['emailVerified'])

    def test_migration_is_additive_and_idempotent(self):
        payment={'_id':ObjectId(),'business_id':'lumiere-mayfair','status':'succeeded'};self.raw.payments.insert_one(payment)
        site=self.raw.restaurants.find_one({'restaurantId':'lumiere-mayfair'})
        self.raw.restaurants.update_one({'_id':site['_id']},{'$unset':{'organizationId':1}})
        self.raw.page_configs.delete_many({'restaurantId':'lumiere-mayfair'})
        migrate(self.raw,True);migrate(self.raw,True)
        self.assertEqual(self.raw.payments.find_one({'_id':payment['_id']}),payment)
        self.assertEqual(self.raw.organizations.count_documents({}),7)
        self.assertEqual(self.raw.restaurants.find_one({'_id':site['_id']})['restaurantId'],'lumiere-mayfair')
        self.assertEqual(self.raw.page_configs.count_documents({'restaurantId':'lumiere-mayfair','enabled':True,'published.enabled':True}),4)
        self.raw.homepage_sections.update_one({'restaurantId':'lumiere-mayfair','type':'hero'}, {'$set':{'templateVariant':'b','publishedTemplateVariant':'c','publishedVisible':False}})
        migrate(self.raw,True)
        section=self.raw.homepage_sections.find_one({'restaurantId':'lumiere-mayfair','type':'hero'})
        self.assertEqual(section['publishedTemplateVariant'],'c')
        self.assertFalse(section['publishedVisible'])

    def test_duplicate_email_allowed_only_in_different_organizations(self):
        h,u=self.login();base={'name':'Staff','email':'same@example.com','password':'StrongPass123','role':'staff','siteAccess':['lumiere-mayfair']}
        self.assertEqual(self.client.post('/api/v1/restaurants/lumiere-mayfair/users',headers=h,json=base).status_code,201)
        h2,_=self.login('PULSEFIT','admin@pulsefit.com')
        self.assertEqual(self.client.post('/api/v1/restaurants/site_pulsefit/users',headers=h2,json={**base,'siteAccess':['site_pulsefit']}).status_code,201)

    def test_owner_can_create_and_switch_sites_without_accessing_other_orgs(self):
        h,u=self.login()
        r=self.client.post('/api/v1/organizations/'+u['organizationId']+'/sites',headers=h,json={'vertical':'retail','slug':'second-site','name':'Second Site'})
        self.assertEqual(r.status_code,201,r.json)
        sid=r.json['id']
        self.assertEqual(self.client.get('/api/v1/admin/reservations?siteId='+sid,headers=h).status_code,200)
        self.assertEqual(self.client.get('/api/v1/sites/'+sid+'/pages?version=draft',headers=h).status_code,200)
        self.assertEqual(self.client.patch('/api/v1/organizations/'+u['organizationId']+'/sites/'+sid,headers=h,json={'vertical':'gym'}).status_code,400)

    def test_account_tokens_are_single_use_and_reset_revokes_sessions(self):
        import hashlib
        from datetime import datetime,timezone,timedelta
        h,u=self.login()
        self.raw.account_tokens.insert_one({'tokenHash':hashlib.sha256(b'one-use').hexdigest(),'userId':ObjectId(u['id']),'purpose':'reset','expiresAt':datetime.now(timezone.utc)+timedelta(hours=1)})
        body={'token':'one-use','password':'ChangedPass123','passwordConfirmation':'ChangedPass123'}
        self.assertEqual(self.client.post('/api/v1/auth/reset-password',json=body).status_code,200)
        self.assertEqual(self.client.post('/api/v1/auth/reset-password',json=body).status_code,400)
        self.assertEqual(self.client.get('/api/v1/auth/me',headers=h).status_code,404)

    def test_suspended_org_cannot_resolve_or_access_site(self):
        h,u=self.login()
        self.raw.organizations.update_one({'_id':ObjectId(u['organizationId'])},{'$set':{'status':'suspended'}})
        self.assertEqual(self.client.get('/api/v1/public/sites/resolve?slug=lumiere-mayfair').status_code,404)
        self.assertEqual(self.client.get('/api/v1/restaurants/lumiere-mayfair/menu',headers=h).status_code,404)

    def test_invalid_bodies_and_missing_email_delivery_are_explicit(self):
        self.assertEqual(self.client.post('/api/v1/auth/login',json=['bad']).status_code,400)
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'orgId':'LUMIERE','email':'admin@lumiere.com','password':123}).status_code,401)
        h,u=self.login()
        self.raw.restaurant_users.update_one({'_id':ObjectId(u['id'])},{'$set':{'emailVerified':False}})
        self.assertEqual(self.client.post('/api/v1/auth/resend-verification',headers=h).status_code,503)
        self.assertEqual(self.client.post('/api/v1/restaurants/lumiere-mayfair/publish',headers=h).status_code,403)

    def test_branding_badge_is_platform_only(self):
        from werkzeug.security import generate_password_hash
        h,_=self.login()
        url='/api/v1/superadmin/sites/site_pulsefit/branding-badge'
        self.assertEqual(self.client.patch(url,headers=h,json={'enabled':False}).status_code,403)
        self.raw.restaurant_users.insert_one({'email':'platform@example.com','name':'Platform Admin','role':'super_admin','isActive':True,'passwordHash':generate_password_hash('PlatformPass123')})
        response=self.client.post('/api/v1/auth/super-admin-login',json={'email':'platform@example.com','password':'PlatformPass123'})
        self.assertEqual(response.status_code,200)
        platform={'Authorization':'Bearer '+response.json['token']}
        self.assertEqual(self.client.patch(url,headers=platform,json={'enabled':False}).status_code,200)
        self.assertFalse(self.client.get('/api/v1/public/sites/site_pulsefit/branding-badge').json['enabled'])
        self.assertEqual(self.client.post('/api/v1/organizations/'+str(ObjectId())+'/sites',headers=platform,json={'name':'Orphan','vertical':'gym','slug':'orphan'}).status_code,404)

    def test_super_admin_cannot_manage_an_orgs_users(self):
        from werkzeug.security import generate_password_hash
        self.raw.restaurant_users.insert_one({'email':'platform2@example.com','name':'Platform Admin','role':'super_admin','isActive':True,'passwordHash':generate_password_hash('PlatformPass123')})
        response=self.client.post('/api/v1/auth/super-admin-login',json={'email':'platform2@example.com','password':'PlatformPass123'})
        self.assertEqual(response.status_code,200)
        platform={'Authorization':'Bearer '+response.json['token']}
        self.assertEqual(self.client.get('/api/v1/restaurants/lumiere-mayfair/users',headers=platform).status_code,403)
        self.assertEqual(self.client.post('/api/v1/restaurants/lumiere-mayfair/users',headers=platform,json={'email':'new@example.com','name':'New Staff','password':'StrongPass123'}).status_code,403)
        # Reading the Site itself (support/impersonation per Plan §5.1) is still allowed - only Staff management is not.
        self.assertEqual(self.client.get('/api/v1/restaurants/lumiere-mayfair/homepage?version=draft',headers=platform).status_code,200)


if __name__=='__main__': unittest.main()
