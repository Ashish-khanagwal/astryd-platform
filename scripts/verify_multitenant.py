"""Staging-only Atlas contract checks; transaction probes are explicitly aborted."""
import os
import secrets
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['ASTRYD_ENV']='staging'
from wsgi import app
from app.extensions import mongo
from app.platform.service import insert_site


def main():
    with app.app_context():
        db=mongo.db
        if db.name!='astryd_staging':raise RuntimeError('Verification restricted to isolated staging')
        client=app.test_client()
        for code,address,sid in [('LUMIERE','admin@lumiere.com','lumiere-mayfair'),('LUMIERENH','admin@lumierenottinghill.com','rest_lumiere_nh'),('PULSEFIT','admin@pulsefit.com','site_pulsefit'),('NOVAGOODS','admin@novagoods.com','site_novagoods')]:
            login=client.post('/api/v1/auth/login',json={'orgId':code,'email':address,'password':'admin123'})
            assert login.status_code==200,code+' login failed'
            headers={'Authorization':'Bearer '+login.json['token']}
            org=login.json['user']['organizationId']
            sites=client.get(f'/api/v1/organizations/{org}/sites',headers=headers)
            assert [s['id'] for s in sites.json]==[sid],code+' must only access its own demo business'
            for url in [f'/organizations/{org}/sites',f'/restaurants/{sid}/brand?version=draft',f'/restaurants/{sid}/homepage?version=draft',f'/restaurants/{sid}/menu',f'/restaurants/{sid}/media',f'/restaurants/{sid}/users',f'/restaurants/{sid}/audit-logs',f'/sites/{sid}/pages?version=draft',f'/restaurants/{sid}/page-content?version=draft',f'/sites/{sid}/membership/plans',f'/sites/{sid}/membership/members',f'/orders/restaurant/{sid}',f'/admin/reservations?siteId={sid}',f'/admin/reservation-availability?siteId={sid}']:
                response=client.get('/api/v1'+url,headers=headers)
                assert response.status_code==200,f'{code} {url}: {response.status_code}'
            other='site_pulsefit' if sid!='site_pulsefit' else 'site_novagoods'
            assert client.get(f'/api/v1/admin/reservations?siteId={other}',headers=headers).status_code==403
            print(code+': login, all data contracts and cross-tenant denial verified')
        org=db.organizations.find_one({'code':'LUMIERE'})
        slug='transaction-probe-'+secrets.token_hex(6)
        with db.client.start_session() as session:
            session.start_transaction()
            site=insert_site(db,str(org['_id']),'probe',{'vertical':'gym','slug':slug,'siteName':'Transaction probe'},session,'')
            assert db.page_configs.count_documents({'restaurantId':site['restaurantId']},session=session)==4
            session.abort_transaction()
        assert db.restaurants.find_one({'slug':slug}) is None
        assert db.page_configs.count_documents({'restaurantId':site['restaurantId']})==0
        print('Atlas multi-collection transaction and rollback verified; probe records not persisted')


if __name__=='__main__':main()
