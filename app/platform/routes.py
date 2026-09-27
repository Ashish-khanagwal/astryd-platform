"""Organization, Site, page configuration and manual membership APIs."""
from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required, verify_jwt_in_request
from pymongo import ReturnDocument
from app.common.api import current_identity, require_restaurant, serialize_doc
from app.extensions import mongo
from app.platform.service import MODULES, insert_site, now, text, email

bp = Blueprint('platform', __name__, url_prefix='')


def site_doc(doc):
    result = serialize_doc(doc)
    result['id'] = doc['restaurantId']
    return result


def org_access(oid, owner=False):
    user = current_identity()
    try:
        org = mongo.db.organizations.find_one({'_id':ObjectId(oid)})
    except (ValueError,TypeError):
        org = None
    if user.get('role') == 'super_admin':
        return (user,None) if org else (None,(jsonify(error='not_found'),404))
    if not org or org.get('status')!='active' or user.get('organizationId') != oid or (owner and user.get('role') != 'owner'):
        return None, (jsonify(error='forbidden', message='Organization access denied'), 403)
    return user, None


def public_site(sid):
    return mongo.db.restaurants.find_one({'restaurantId': sid, 'status': {'$ne': 'suspended'}})


@bp.post('/organizations')
@bp.post('/orgs')
@jwt_required()
def organization_create():
    if current_identity().get('role')!='super_admin':
        return jsonify(error='forbidden'),403
    from app.platform.service import provision
    try:
        user,code=provision(mongo.db,request.get_json(silent=True) or {},current_app.config.get('PLATFORM_DOMAIN',''))
    except ValueError as exc:
        return jsonify(error='validation_error',message=str(exc)),400
    from app.platform.accounts import issue_link
    if current_app.config.get('REQUIRE_EMAIL_VERIFICATION_FOR_PUBLISH',True):issue_link(user,'verify')
    return jsonify(organizationId=user['organizationId'],orgCode=code,owner=serialize_doc(user)),201


@bp.route('/organizations/<oid>',methods=['GET','PATCH'])
@bp.route('/orgs/<oid>',methods=['GET','PATCH'])
@jwt_required()
def organization_detail(oid):
    _,failure=org_access(oid)
    if failure:return failure
    try: identifier=ObjectId(oid)
    except (ValueError,TypeError):return jsonify(error='not_found'),404
    if request.method=='PATCH':
        if current_identity().get('role')!='super_admin':return jsonify(error='forbidden'),403
        body=request.get_json(silent=True) or {}
        if set(body)-{'name','status'} or body.get('status','active') not in {'active','suspended'}:return jsonify(error='validation_error'),400
        if 'name' in body:
            try:body['name']=text(body['name'],'name')
            except ValueError as exc:return jsonify(error='validation_error',message=str(exc)),400
        mongo.db.organizations.update_one({'_id':identifier},{'$set':{**body,'updatedAt':now()}})
    doc=mongo.db.organizations.find_one({'_id':identifier})
    return jsonify(serialize_doc(doc)) if doc else (jsonify(error='not_found'),404)


@bp.get('/organizations/<oid>/sites')
@bp.get('/orgs/<oid>/sites')
@jwt_required()
def sites(oid):
    user, failure = org_access(oid)
    if failure:
        return failure
    query = {'organizationId': oid}
    if user.get('role') != 'super_admin' and user.get('siteAccess') != 'all':
        query['restaurantId'] = {'$in': user.get('siteAccess', [user.get('restaurantId')])}
    return jsonify([site_doc(d) for d in mongo.db.restaurants.find(query)])


@bp.post('/organizations/<oid>/sites')
@bp.post('/orgs/<oid>/sites')
@jwt_required()
def site_create(oid):
    user, failure = org_access(oid, owner=True)
    if failure:
        return failure
    try:
        with mongo.db.client.start_session() as session:
            doc = session.with_transaction(lambda s: insert_site(mongo.db, oid, user['userId'], request.get_json(silent=True) or {}, s, current_app.config.get('PLATFORM_DOMAIN', '')))
    except ValueError as exc:
        return jsonify(error='validation_error', message=str(exc)), 400
    return jsonify(site_doc(doc)), 201


@bp.patch('/organizations/<oid>/sites/<sid>')
@bp.patch('/orgs/<oid>/sites/<sid>')
@jwt_required()
def site_update(oid, sid):
    _, failure = org_access(oid, owner=True)
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    if set(body) - {'name', 'status'} or body.get('status', 'active') not in {'active', 'suspended', 'trial'}:
        return jsonify(error='validation_error', message='Only name and status can be changed'), 400
    try:
        values = {'updatedAt': now()}
        if 'name' in body:
            values['name'] = text(body['name'], 'name')
        if 'status' in body:
            values['status'] = body['status']
    except ValueError as exc:
        return jsonify(error='validation_error', message=str(exc)), 400
    doc = mongo.db.restaurants.find_one_and_update({'organizationId': oid, 'restaurantId': sid}, {'$set': values}, return_document=ReturnDocument.AFTER)
    return (jsonify(site_doc(doc)), 200) if doc else (jsonify(error='not_found'), 404)


@bp.delete('/organizations/<oid>/sites/<sid>')
@bp.delete('/orgs/<oid>/sites/<sid>')
@jwt_required()
def site_archive(oid,sid):
    _,failure=org_access(oid,owner=True)
    if failure:return failure
    result=mongo.db.restaurants.update_one({'organizationId':oid,'restaurantId':sid},{'$set':{'status':'suspended','updatedAt':now()}})
    return (jsonify(message='Site suspended; historical records retained'),200) if result.matched_count else (jsonify(error='not_found'),404)


@bp.get('/superadmin/sites')
@jwt_required()
def all_sites():
    if current_identity().get('role') != 'super_admin':
        return jsonify(error='forbidden'), 403
    organizations = {str(d['_id']): d['name'] for d in mongo.db.organizations.find({})}
    return jsonify([{**site_doc(d), 'organizationName': organizations.get(d.get('organizationId'), '')} for d in mongo.db.restaurants.find({})])


@bp.patch('/superadmin/sites/<sid>/branding-badge')
@jwt_required()
def badge_update(sid):
    if current_identity().get('role') != 'super_admin':
        return jsonify(error='forbidden'), 403
    enabled = (request.get_json(silent=True) or {}).get('enabled')
    if not isinstance(enabled, bool):
        return jsonify(error='validation_error'), 400
    doc = mongo.db.restaurants.find_one_and_update({'restaurantId': sid}, {'$set': {'brandingBadgeEnabled': enabled}}, return_document=ReturnDocument.AFTER)
    return jsonify(site_doc(doc)) if doc else (jsonify(error='not_found'), 404)


@bp.get('/public/sites/resolve')
def resolve():
    slug = request.args.get('slug', '').lower()
    hostname = request.args.get('hostname', '').lower().split(':')[0]
    query = {'slug': slug} if slug else {'slug': '__unresolved__'}
    domain = current_app.config.get('PLATFORM_DOMAIN', '')
    if not slug and domain and hostname.endswith('.' + domain):
        query = {'slug': hostname[:-len(domain) - 1]}
    doc = mongo.db.restaurants.find_one({**query, 'status': {'$ne': 'suspended'}})
    if doc:
        if doc.get('organizationId'):
            org=mongo.db.organizations.find_one({'_id':ObjectId(doc['organizationId'])})
            if not org or org.get('status')!='active':return jsonify(error='not_found',message='Site not found'),404
        website=mongo.db.website_settings.find_one({'restaurantId':doc['restaurantId']})
        if website and website.get('publishStatus')!='published':doc=None
    return jsonify(site_doc(doc)) if doc else (jsonify(error='not_found', message='Site not found'), 404)


@bp.get('/public/sites/<sid>/branding-badge')
def badge(sid):
    doc = public_site(sid)
    return jsonify(enabled=doc.get('brandingBadgeEnabled', True), vertical=doc.get('vertical', 'restaurant')) if doc else (jsonify(error='not_found'), 404)


@bp.get('/sites/<sid>/pages')
def pages(sid):
    if not public_site(sid):
        return jsonify(error='not_found'), 404
    version = request.args.get('version', 'published')
    if version not in {'draft','published'}:return jsonify(error='validation_error'),400
    if version == 'draft':
        verify_jwt_in_request()
        _, failure = require_restaurant(sid)
        if failure:
            return failure
    docs = list(mongo.db.page_configs.find({'restaurantId': sid}).sort('order', 1))
    return jsonify([serialize_doc(d if version == 'draft' else {**d, **d.get('published', {})}) for d in docs])


@bp.patch('/sites/<sid>/pages/<module>')
@jwt_required()
def page_update(sid, module):
    _, failure = require_restaurant(sid, permission='settings')
    if failure:
        return failure
    body = request.get_json(silent=True) or {}
    if module not in MODULES or set(body) - {'enabled', 'navLabel', 'order', 'templateVariant'}:
        return jsonify(error='validation_error'), 400
    if ('enabled' in body and not isinstance(body['enabled'], bool)) or ('order' in body and (type(body['order']) != int or not 0 <= body['order'] <= 3)) or ('templateVariant' in body and body['templateVariant'] not in {'a', 'b', 'c'}):
        return jsonify(error='validation_error'), 400
    try:
        if 'navLabel' in body:
            body['navLabel'] = text(body['navLabel'], 'navLabel', 60)
    except ValueError as exc:
        return jsonify(error='validation_error', message=str(exc)), 400
    doc = mongo.db.page_configs.find_one_and_update({'restaurantId': sid, 'module': module}, {'$set': {**body, 'updatedAt': now()}}, return_document=ReturnDocument.AFTER)
    return jsonify(serialize_doc(doc)) if doc else (jsonify(error='not_found'), 404)


@bp.get('/restaurants/<sid>/page-content')
def content(sid):
    version = request.args.get('version', 'published')
    if version not in {'draft', 'published'}:
        return jsonify(error='validation_error'), 400
    if not public_site(sid):
        return jsonify(error='not_found'), 404
    if version == 'draft':
        verify_jwt_in_request()
        _, failure = require_restaurant(sid)
        if failure:
            return failure
    return jsonify((mongo.db.page_content.find_one({'restaurantId': sid}) or {}).get(version, {}))


@bp.put('/restaurants/<sid>/page-content/<page>')
@jwt_required()
def content_update(sid, page):
    _, failure = require_restaurant(sid, permission='homepage')
    if failure:
        return failure
    fields = (request.get_json(silent=True) or {}).get('fields')
    if page not in {'landing', 'items', 'catalog', 'booking', 'membership', 'checkout', 'global'} or not isinstance(fields, dict) or any('.' in k or k.startswith('$') for k in fields):
        return jsonify(error='validation_error'), 400
    mongo.db.page_content.update_one({'restaurantId': sid}, {'$set': {f'draft.{page}': fields, 'updatedAt': now()}, '$setOnInsert': {'restaurantId': sid, 'published': {}}}, upsert=True)
    return jsonify(page=page, fields=fields)
