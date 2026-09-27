"""Atlas signup/publishing probes inside an explicitly aborted transaction; no payments."""
import argparse
import os
import secrets
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TransactionCollection:
    def __init__(self, collection, session):
        self.collection, self.session = collection, session

    def __getattr__(self, name):
        method = getattr(self.collection, name)
        def execute(*args, **kwargs):
            kwargs['session'] = self.session
            return method(*args, **kwargs)
        return execute


class BorrowedSession:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def with_transaction(self, callback):
        return callback(self.session)


class TransactionDatabase:
    def __init__(self, db, session):
        self.db, self.session, self.name = db, session, db.name
        self.client = self

    def start_session(self):
        return BorrowedSession(self.session)

    def __getitem__(self, name):
        return TransactionCollection(self.db[name], self.session)

    def __getattr__(self, name):
        return self[name]


def verify(app, mongo):
    with app.app_context():
        db = mongo.db
        assert app.config['REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH'] is False
        slugs = []
        with db.client.start_session() as session:
            session.start_transaction()
            try:
                with patch.object(mongo, 'db', TransactionDatabase(db, session)):
                    client = app.test_client()
                    previous_headers = None
                    for vertical in ('restaurant', 'gym', 'retail', 'salon'):
                        slug = 'parity-probe-' + secrets.token_hex(6)
                        slugs.append(slug)
                        body = dict(vertical=vertical, slug=slug, siteName='Parity probe',
                                    organizationName=slug, ownerName='Probe owner',
                                    ownerEmail=slug+'@example.invalid', password='ParityProbe123',
                                    passwordConfirmation='ParityProbe123')
                        response = client.post('/api/v1/auth/signup', json=body)
                        assert response.status_code == 201, (vertical, response.status_code)
                        assert response.json['emailVerificationRequiredForPublish'] is False
                        user = response.json['user']
                        assert user['emailVerified'] is False
                        sid = user['restaurantId']
                        headers = {'Authorization': 'Bearer '+response.json['token']}
                        login = client.post('/api/v1/auth/login', json={
                            'orgId':response.json['orgCode'], 'email':body['ownerEmail'],
                            'password':body['password']})
                        assert login.status_code == 200
                        if previous_headers:
                            assert client.get(f'/api/v1/restaurants/{sid}/brand?version=draft', headers=previous_headers).status_code == 403
                        previous_headers = headers
                        assert client.get('/api/v1/public/sites/resolve?slug='+slug).status_code == 404
                        assert client.put(f'/api/v1/restaurants/{sid}/brand', headers=headers, json={'tagline':'Published probe'}).status_code == 200
                        assert client.post(f'/api/v1/restaurants/{sid}/publish', headers=headers).status_code == 200
                        settings = mongo.db.website_settings.find_one({'restaurantId':sid})
                        assert settings['publishStatus'] == 'published' and settings['publishedAt'] is not None
                        assert client.get('/api/v1/public/sites/resolve?slug='+slug).status_code == 200
                        assert client.put(f'/api/v1/restaurants/{sid}/brand', headers=headers, json={'tagline':'Draft only'}).status_code == 200
                        brand = mongo.db.brand_settings.find_one({'restaurantId':sid})
                        assert brand['draft']['tagline'] == 'Draft only'
                        assert brand['published']['tagline'] == 'Published probe'
                        print(vertical+': signup, login, isolation, publish timestamp and draft/live separation passed')
            finally:
                session.abort_transaction()
        assert db.restaurants.count_documents({'slug':{'$in':slugs}}) == 0
        print(db.name+': transaction aborted; no probe tenants or payments persisted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--environment', choices=['staging', 'production'], required=True)
    args = parser.parse_args()
    os.environ['ASTRYD_ENV'] = args.environment
    os.environ['MONGO_CREATE_INDEXES'] = 'false'
    from wsgi import app
    from app.extensions import mongo
    verify(app, mongo)
