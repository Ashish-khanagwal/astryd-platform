"""Site-scoped memberships with one-time paid public enrollment; no recurring billing."""
import hashlib
import secrets
from datetime import datetime, timedelta
from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required, verify_jwt_in_request
from pymongo import ReturnDocument
from app.common.api import current_identity, require_restaurant, serialize_doc
from app.extensions import mongo
from app.platform.accounts import limited
from app.platform.service import email, now, text

bp = Blueprint('membership', __name__, url_prefix='')


@bp.post('/sites/<sid>/membership/checkout')
def membership_checkout(sid):
    # Always a public paid enrollment, even inside an authenticated draft preview.
    if limited('membership-checkout',10):return jsonify(error='rate_limited'),429
    if not mongo.db.restaurants.find_one({'restaurantId':sid,'status':{'$ne':'suspended'}}):return jsonify(error='not_found'),404
    try:
        body=request.get_json(silent=True) or {}
        values={'planId':text(body.get('planId'),'planId'),'customerName':text(body.get('customerName'),'customerName'),'customerEmail':email(body.get('customerEmail')),'customerPhone':text(body.get('customerPhone'),'customerPhone')}
        plan=mongo.db.membership_plans.find_one({'_id':oid(values['planId']),'restaurantId':sid,'isActive':True,'deletedAt':None})
        if not plan:raise ValueError('Plan is not available for this Site')
        amount=current_app.config.get('PAYMENT_AMOUNT_OVERRIDE_CENTS')
        if amount is None:amount=plan['priceCents']
        if type(amount)!=int or amount<=0:raise ValueError('This plan has no paid enrollment price; contact the business')
    except (ValueError,TypeError) as exc:return jsonify(error='validation_error',message=str(exc)),400
    from app.payments.service import create_payment_attempt,serialize_payment
    def provision(session):
        doc={'_id':ObjectId(),'restaurantId':sid,**values,'status':'paused','paymentStatus':'created','startDate':now().date().isoformat(),'nextBillingDate':None,'createdAt':now(),'updatedAt':now()}
        payment,secret=create_payment_attempt(mongo.db,business_id=sid,context_type='membership',context_id=doc['_id'],amount_cents=amount,customer={'name':values['customerName'],'email':values['customerEmail'],'phone':values['customerPhone']},snapshot={'planName':plan['name'],'planId':values['planId']},session=session)
        doc['paymentId']=payment['_id']
        mongo.db.members.insert_one(doc,session=session)
        return payment,secret
    with mongo.db.client.start_session() as session:payment,secret=session.with_transaction(provision)
    return jsonify(**serialize_payment(mongo.db,payment),checkout_secret=secret),201


def oid(value):
    try:
        return ObjectId(value)
    except (ValueError, TypeError):
        return None


def gate(sid):
    return require_restaurant(sid, permission='membership')


def plan_values(body):
    allowed = {'name', 'description', 'priceCents', 'billingInterval', 'benefits', 'isActive', 'order'}
    if set(body) - allowed:
        raise ValueError('Unknown plan field')
    if 'name' in body:
        body['name'] = text(body['name'], 'name')
    if 'priceCents' in body and (type(body['priceCents']) != int or not 0 <= body['priceCents'] <= 10000000):
        raise ValueError('priceCents must be a non-negative integer')
    if 'billingInterval' in body and body['billingInterval'] not in {'one_time', 'monthly', 'quarterly', 'yearly'}:
        raise ValueError('Invalid billing interval')
    if 'benefits' in body and (not isinstance(body['benefits'], list) or any(not isinstance(v, str) for v in body['benefits'])):
        raise ValueError('Invalid benefits')
    if 'isActive' in body and not isinstance(body['isActive'], bool):
        raise ValueError('Invalid active flag')
    if 'order' in body and (type(body['order']) != int or not 0 <= body['order'] <= 10000):
        raise ValueError('Invalid order')
    return body


def member_values(body):
    if set(body) - {'planId', 'customerName', 'customerEmail', 'customerPhone', 'status', 'startDate', 'nextBillingDate', 'notes'}:
        raise ValueError('Unknown member field')
    if 'customerName' in body:
        body['customerName'] = text(body['customerName'], 'customerName')
    if 'customerEmail' in body:
        body['customerEmail'] = email(body['customerEmail'])
    if 'status' in body and body['status'] not in {'active', 'paused', 'cancelled', 'expired'}:
        raise ValueError('Invalid member status')
    for key in ('startDate', 'nextBillingDate'):
        if body.get(key) is not None:
            datetime.strptime(body[key], '%Y-%m-%d')
    for key in ('customerPhone', 'notes'):
        if key in body and (not isinstance(body[key], str) or len(body[key]) > 2000):
            raise ValueError('Invalid text field')
    return body


@bp.get('/sites/<sid>/membership/plans')
def plans(sid):
    if not mongo.db.restaurants.find_one({'restaurantId': sid, 'status': {'$ne': 'suspended'}}):
        return jsonify(error='not_found'), 404
    verify_jwt_in_request(optional=True)
    query = {'restaurantId': sid, 'deletedAt': None}
    if not current_identity() or gate(sid)[1]:
        query['isActive'] = True
    return jsonify(serialize_doc(list(mongo.db.membership_plans.find(query).sort('order', 1))))


@bp.post('/sites/<sid>/membership/plans')
@bp.patch('/sites/<sid>/membership/plans/<pid>')
@jwt_required()
def plan_write(sid, pid=None):
    _, failure = gate(sid)
    if failure:
        return failure
    try:
        values = plan_values(request.get_json(silent=True) or {})
        if pid is None and ('name' not in values or 'priceCents' not in values):
            raise ValueError('name and priceCents are required')
    except (ValueError, TypeError) as exc:
        return jsonify(error='validation_error', message=str(exc)), 400
    if pid is None:
        doc = {'restaurantId': sid, 'description': '', 'billingInterval': 'one_time', 'benefits': [], 'isActive': True, 'order': 0, 'createdAt': now(), 'updatedAt': now(), **values}
        doc['_id'] = mongo.db.membership_plans.insert_one(doc).inserted_id
        return jsonify(serialize_doc(doc)), 201
    doc = mongo.db.membership_plans.find_one_and_update({'_id': oid(pid), 'restaurantId': sid, 'deletedAt': None}, {'$set': {**values, 'updatedAt': now()}}, return_document=ReturnDocument.AFTER)
    return jsonify(serialize_doc(doc)) if doc else (jsonify(error='not_found'), 404)


@bp.delete('/sites/<sid>/membership/plans/<pid>')
@jwt_required()
def plan_delete(sid, pid):
    _, failure = gate(sid)
    if failure:
        return failure
    result = mongo.db.membership_plans.update_one({'_id': oid(pid), 'restaurantId': sid}, {'$set': {'deletedAt': now(), 'isActive': False}})
    return (jsonify(message='Plan archived'), 200) if result.matched_count else (jsonify(error='not_found'), 404)


@bp.get('/sites/<sid>/membership/members')
@jwt_required()
def members(sid):
    _, failure = gate(sid)
    if failure:
        return failure
    query = {'restaurantId': sid, 'deletedAt': None}
    if request.args.get('email'):
        query['customerEmail'] = request.args['email'].strip().lower()
    return jsonify(serialize_doc(list(mongo.db.members.find(query).limit(1000))))


@bp.post('/sites/<sid>/membership/members')
def member_create(sid):
    verify_jwt_in_request(optional=True)
    admin = bool(current_identity())
    if admin:
        _, failure = gate(sid)
        if failure:
            return failure
    else:
        return jsonify(error='payment_required',message='Use membership checkout to pay before activation'),402
    if not mongo.db.restaurants.find_one({'restaurantId': sid, 'status': {'$ne': 'suspended'}}):
        return jsonify(error='not_found'), 404
    try:
        values = member_values(request.get_json(silent=True) or {})
        for key in ('customerName', 'customerEmail', 'planId'):
            if not values.get(key):
                raise ValueError(f'{key} is required')
        if not mongo.db.membership_plans.find_one({'_id': oid(values['planId']), 'restaurantId': sid, 'isActive': True, 'deletedAt': None}):
            raise ValueError('Plan is not available for this Site')
    except (ValueError, TypeError) as exc:
        return jsonify(error='validation_error', message=str(exc)), 400
    if not admin:
        # A public request cannot grant paid entitlements or write internal notes.
        values = {k: v for k, v in values.items() if k not in {'status', 'notes', 'nextBillingDate'}}
        values['status'] = 'paused'
    doc = {'restaurantId': sid, 'customerPhone': '', 'status': 'active', 'startDate': now().date().isoformat(), 'nextBillingDate': None, 'createdAt': now(), 'updatedAt': now(), **values}
    try:
        doc['_id'] = mongo.db.members.insert_one(doc).inserted_id
    except __import__('pymongo').errors.DuplicateKeyError:
        return jsonify(error='conflict', message='A membership request already exists; contact this business'), 409
    return jsonify(serialize_doc(doc)), 201


@bp.patch('/sites/<sid>/membership/members/<mid>')
@bp.delete('/sites/<sid>/membership/members/<mid>')
@jwt_required()
def member_update(sid, mid):
    _, failure = gate(sid)
    if failure:
        return failure
    try:
        values = {'deletedAt': now(), 'status': 'cancelled'} if request.method == 'DELETE' else member_values(request.get_json(silent=True) or {})
        existing = mongo.db.members.find_one({'_id': oid(mid), 'restaurantId': sid, 'deletedAt': None})
        if existing and existing.get('paymentId') and existing.get('paymentStatus') != 'succeeded' and values.get('status') == 'active':
            return jsonify(error='payment_required', message='Payment must succeed before activation'),409
        if 'planId' in values and not mongo.db.membership_plans.find_one({'_id': oid(values['planId']), 'restaurantId': sid, 'deletedAt': None}):
            raise ValueError('Plan does not belong to this Site')
    except (ValueError, TypeError) as exc:
        return jsonify(error='validation_error', message=str(exc)), 400
    doc = mongo.db.members.find_one_and_update({'_id': oid(mid), 'restaurantId': sid, 'deletedAt': None}, {'$set': {**values, 'updatedAt': now()}}, return_document=ReturnDocument.AFTER)
    if not doc:
        return jsonify(error='not_found'), 404
    return jsonify(message='Member archived') if request.method == 'DELETE' else jsonify(serialize_doc(doc))


@bp.route('/sites/<sid>/membership/members/<mid>/check-ins', methods=['GET', 'POST'])
@jwt_required()
def checkins(sid, mid):
    _, failure = gate(sid)
    if failure:
        return failure
    member = mongo.db.members.find_one({'_id': oid(mid), 'restaurantId': sid, 'deletedAt': None})
    if not member:
        return jsonify(error='not_found'), 404
    if request.method == 'GET':
        return jsonify(serialize_doc(list(mongo.db.member_check_ins.find({'restaurantId': sid, 'memberId': mid}).sort('checkedInAt', -1).limit(200))))
    if member['status'] != 'active':
        return jsonify(error='invalid_state', message='Membership is not active'), 409
    doc = {'restaurantId': sid, 'memberId': mid, 'checkedInAt': now()}
    doc['_id'] = mongo.db.member_check_ins.insert_one(doc).inserted_id
    return jsonify(serialize_doc(doc)), 201


@bp.post('/sites/<sid>/membership/lookup')
def lookup(sid):
    if limited('membership-lookup', 5):
        return jsonify(error='rate_limited'), 429
    if not current_app.config.get('SMTP_HOST'):
        return jsonify(error='email_unavailable', message='Please contact the business to look up your membership'), 503
    address = str((request.get_json(silent=True) or {}).get('email', '')).strip().lower()
    member = mongo.db.members.find_one({'restaurantId': sid, 'customerEmail': address, 'deletedAt': None})
    site = mongo.db.restaurants.find_one({'restaurantId': sid, 'status': {'$ne': 'suspended'}})
    if member and site:
        token = secrets.token_urlsafe(32)
        mongo.db.account_tokens.insert_one({'tokenHash': hashlib.sha256(token.encode()).hexdigest(), 'memberId': member['_id'], 'restaurantId': sid, 'purpose': 'membership', 'expiresAt': now() + timedelta(hours=1)})
        from app.notifications.tasks import send_account_email
        link = current_app.config['PLATFORM_ADMIN_URL'].rstrip('/') + f'/s/{site["slug"]}?memberToken={token}#/membership'
        try:
            send_account_email.delay(address, 'view membership', link)
        except Exception:
            current_app.logger.error('Membership email could not be queued')
            return jsonify(error='email_unavailable',message='Please contact the business to look up your membership'),503
    return jsonify(message='If a membership exists, a private link has been emailed')


@bp.post('/sites/<sid>/membership/lookup/verify')
def lookup_verify(sid):
    from app.platform.accounts import consume
    if limited('membership-verify'):
        return jsonify(error='rate_limited'), 429
    body = request.get_json(silent=True) or {}
    # Scope before consuming so a wrong-site request cannot consume another Site's link.
    raw = str(body.get('token', ''))
    found = mongo.db.account_tokens.find_one({'tokenHash': hashlib.sha256(raw.encode()).hexdigest(), 'restaurantId': sid, 'purpose': 'membership', 'expiresAt': {'$gt': now()}})
    record = consume(raw, ['membership']) if found else None
    if not record:
        return jsonify(error='invalid_token', message='Link is invalid or expired'), 400
    member = mongo.db.members.find_one({'_id': record['memberId'], 'restaurantId': sid, 'deletedAt': None})
    if not member:
        return jsonify(error='not_found'), 404
    member.pop('notes', None)
    logs = list(mongo.db.member_check_ins.find({'restaurantId': sid, 'memberId': str(member['_id'])}).limit(200))
    return jsonify(member=serialize_doc(member), checkIns=serialize_doc(logs))
