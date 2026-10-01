from math import ceil
from datetime import datetime
from bson import ObjectId

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pymongo import ReturnDocument

from app.extensions import mongo
from app.integrations.website_sync import enqueue
from app.availability.service import (
    NotFoundError,
    generate_time_slots,
    get_availability_config,
    update_availability_config,
)
from app.common.api import current_identity, log_audit_event, require_restaurant, serialize_doc
from werkzeug.security import generate_password_hash

bp = Blueprint("admin", __name__, url_prefix="")

RESERVATION_STATUSES = {"confirmed", "seated", "completed", "cancelled", "no_show"}


@bp.get("/admin/reservation-availability")
@jwt_required()
def reservation_availability_get():
    """Return real booking availability for the authenticated business."""
    business_id = _authenticated_business_id()
    try:
        return jsonify(get_availability_config(mongo.db, business_id))
    except NotFoundError as exc:
        return jsonify(error="not_found", message=str(exc)), 404


@bp.put("/admin/reservation-availability")
@jwt_required()
def reservation_availability_put():
    """Persist real booking availability for the authenticated business."""
    payload = request.get_json(silent=True)
    error = _validate_availability_payload(payload)
    if error:
        return jsonify(error="validation_error", message=error), 400
    business_id = _authenticated_business_id()
    try:
        settings = update_availability_config(
            mongo.db,
            business_id,
            payload["operating_hours"],
            payload["blocked_dates"],
        )
    except NotFoundError as exc:
        return jsonify(error="not_found", message=str(exc)), 404
    return jsonify(settings)


def _authenticated_business_id():
    from werkzeug.exceptions import Forbidden
    identity = current_identity()
    site_id = request.args.get('siteId') or identity.get('restaurantId')
    _, failure = require_restaurant(site_id, permission='booking')
    if failure:
        raise Forbidden('Site access denied')
    return site_id


def _validate_availability_payload(payload):
    if not isinstance(payload, dict):
        return "JSON request body is required"
    operating_hours = payload.get("operating_hours")
    blocked_dates = payload.get("blocked_dates")
    if not isinstance(operating_hours, list) or len(operating_hours) != 7:
        return "operating_hours must contain exactly seven days"
    if not isinstance(blocked_dates, list):
        return "blocked_dates must be an array"

    seen_days = set()
    for hours in operating_hours:
        if not isinstance(hours, dict):
            return "each operating_hours entry must be an object"
        day = hours.get("day_of_week")
        if isinstance(day, bool) or not isinstance(day, int) or day not in range(7) or day in seen_days:
            return "day_of_week must contain each integer from 0 through 6 once"
        seen_days.add(day)
        for field in ("open_time", "close_time"):
            try:
                datetime.strptime(hours.get(field, ""), "%H:%M")
            except (TypeError, ValueError):
                return f"{field} must use HH:MM format"
        if hours["open_time"] == hours["close_time"]:
            return "open_time and close_time must be different"
        duration = hours.get("slot_duration_mins")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 15 <= duration <= 240:
            return "slot_duration_mins must be an integer between 15 and 240"
        maximum = hours.get("max_per_slot")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100:
            return "max_per_slot must be an integer between 1 and 100"
        if not isinstance(hours.get("is_closed"), bool):
            return "is_closed must be a boolean"
        disabled_slots = hours.get("disabled_slots", [])
        if not isinstance(disabled_slots, list) or any(not isinstance(slot, str) for slot in disabled_slots):
            return "disabled_slots must be an array of HH:MM strings"
        generated_slots = set(generate_time_slots(hours["open_time"], hours["close_time"], duration))
        if any(slot not in generated_slots for slot in disabled_slots):
            return "disabled_slots must contain generated slots for that day"

    seen_dates = set()
    for blocked_date in blocked_dates:
        if not isinstance(blocked_date, dict):
            return "each blocked_dates entry must be an object"
        value = blocked_date.get("date")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except (TypeError, ValueError):
            return "blocked date must use YYYY-MM-DD format"
        if value in seen_dates:
            return "blocked_dates must not contain duplicates"
        seen_dates.add(value)
        if not isinstance(blocked_date.get("reason", ""), str):
            return "blocked date reason must be a string"
    return None


@bp.get("/admin/reservations")
@jwt_required()
def list_reservations():
    """List reservations for the business represented by the JWT identity."""
    business_id = _authenticated_business_id()
    date = request.args.get("date")
    status = request.args.get("status")
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(1, int(request.args.get("limit", 20))))
    except ValueError:
        return jsonify(error="validation_error", message="page and limit must be integers"), 400
    criteria = {"business_id": business_id}
    if date:
        criteria["booking.date"] = date
    if status:
        criteria["status"] = status
    total = mongo.db.reservations.count_documents(criteria)
    cursor = mongo.db.reservations.find(criteria).sort("created_at", -1).skip((page - 1) * limit).limit(limit)
    return jsonify(
        reservations=[serialize_doc(reservation) for reservation in cursor],
        total=total,
        page=page,
        limit=limit,
        pages=ceil(total / limit) if total else 0,
    )


@bp.patch("/admin/reservations/<reservation_id>/status")
@jwt_required()
def update_reservation_status(reservation_id):
    """Update one reservation scoped to the authenticated business."""
    business_id = _authenticated_business_id()
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in RESERVATION_STATUSES:
        return jsonify(error="validation_error", message="status is invalid"), 400
    try:
        object_id = ObjectId(reservation_id)
    except Exception:
        return jsonify(error="not_found", message="Reservation not found"), 404

    reservation = mongo.db.reservations.find_one_and_update(
        {"_id": object_id, "business_id": business_id},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if reservation is None:
        return jsonify(error="not_found", message="Reservation not found"), 404
    enqueue("booking", reservation["_id"])
    return jsonify(reservation=serialize_doc(reservation))

def _owner(rid):
 # Managing an Org's own Staff is an Org Owner action, not a Super Admin one (Multi-Vertical Platform
 # Plan §4/§5.1) - a Super Admin isn't a member of any Org, so it never reaches this endpoint.
 return require_restaurant(rid,{"owner","admin"},permission='users')
def _audit(i,a,t,e,s,d=None):log_audit_event(mongo.db,(request.view_args or {}).get('rid',i["restaurantId"]),i["userId"],i["name"],a,t,e,s,d)
@bp.get("/restaurants/<rid>/users")
@jwt_required()
def users(rid):
 i,f=_owner(rid)
 if f:return f
 q={'organizationId':i['organizationId']} if i.get('organizationId') else {'restaurantId':rid}
 return jsonify(serialize_doc(list(mongo.db.restaurant_users.find(q))))
@bp.post("/restaurants/<rid>/users")
@jwt_required()
def user_create(rid):
 i,f=_owner(rid)
 if f:return f
 b=request.get_json(silent=True) or {}
 from flask import current_app
 from app.platform.service import email,text,password,PERMISSIONS
 from app.platform.accounts import issue_link
 import secrets
 if b.get('role','staff')!='staff':return jsonify(error='forbidden',message='Owners may only invite staff'),403
 try:
  address=email(b.get('email'));name=text(b.get('name'),'name');access=b.get('siteAccess',[rid])
  if not isinstance(access,list) or not access or any(not isinstance(s,str) for s in access):raise ValueError('Select at least one Site')
  for sid in access:
   _,failure=require_restaurant(sid)
   site=mongo.db.restaurants.find_one({'restaurantId':sid})
   if failure or not site or site.get('organizationId')!=i.get('organizationId'):raise ValueError('Invalid Site access')
  permissions=b.get('permissions',{'menu':True,'booking':True})
  if not isinstance(permissions,dict) or set(permissions)-set(PERMISSIONS) or any(not isinstance(v,bool) for v in permissions.values()):raise ValueError('Invalid permissions')
  secret=password(b['password']) if b.get('password') else secrets.token_urlsafe(32)
 except (ValueError,TypeError) as exc:return jsonify(error='validation_error',message=str(exc)),400
 if not b.get('password') and not current_app.config.get('SMTP_HOST'):return jsonify(error='email_unavailable',message='Configure account email delivery before inviting staff'),503
 now=datetime.utcnow();d={'restaurantId':rid,'organizationId':i.get('organizationId'),'siteAccess':access,'email':address,'normalizedEmail':address,'name':name,'passwordHash':generate_password_hash(secret),'role':'staff','permissions':permissions,'isActive':True,'emailVerified':False,'createdAt':now,'updatedAt':now};r=mongo.db.restaurant_users.insert_one(d);d['_id']=r.inserted_id
 if not b.get('password'):issue_link(d,'invite')
 _audit(i,'create','restaurant_user',r.inserted_id,'Invited staff');return jsonify(serialize_doc(d)),201
@bp.put("/restaurants/<rid>/users/<uid>")
@jwt_required()
def user_put(rid,uid):
 i,f=_owner(rid)
 if f:return f
 b=request.get_json(silent=True) or {}
 if set(b)-{'name','siteAccess','permissions','isActive','role'} or b.get('role','staff')!='staff':return jsonify(error='forbidden',message='Only staff access can be changed'),403
 if 'siteAccess' in b:
  if not isinstance(b['siteAccess'],list) or not b['siteAccess']:return jsonify(error='validation_error'),400
  for sid in b['siteAccess']:
   _,failure=require_restaurant(sid)
   if failure:return failure
   site=mongo.db.restaurants.find_one({'restaurantId':sid})
   if not site or site.get('organizationId')!=i.get('organizationId'):return jsonify(error='validation_error',message='Invalid Site access'),400
 if 'permissions' in b:
  from app.platform.service import PERMISSIONS
  if not isinstance(b['permissions'],dict) or set(b['permissions'])-set(PERMISSIONS) or any(not isinstance(v,bool) for v in b['permissions'].values()):return jsonify(error='validation_error'),400
 if 'isActive' in b and not isinstance(b['isActive'],bool):return jsonify(error='validation_error'),400
 q={'organizationId':i['organizationId']} if i.get('organizationId') else {'restaurantId':rid}
 b['updatedAt']=datetime.utcnow();d=mongo.db.restaurant_users.find_one_and_update({'_id':ObjectId(uid),'role':'staff',**q},{'$set':b},return_document=True)
 if not d:return jsonify(error="not_found",message="User not found"),404
 _audit(i,"update","restaurant_user",uid,"Updated user",b);return jsonify(serialize_doc(d))
@bp.delete("/restaurants/<rid>/users/<uid>")
@jwt_required()
def user_delete(rid,uid):
 i,f=_owner(rid)
 if f:return f
 if uid==i["userId"]:return jsonify(error="invalid_state",message="Cannot deactivate yourself"),400
 q={'organizationId':i['organizationId']} if i.get('organizationId') else {'restaurantId':rid}
 r=mongo.db.restaurant_users.update_one({'_id':ObjectId(uid),'role':'staff',**q},{'$set':{'isActive':False,'updatedAt':datetime.utcnow()}})
 if not r.matched_count:return jsonify(error="not_found",message="User not found"),404
 _audit(i,"deactivate","restaurant_user",uid,"Deactivated user");return jsonify(message="User deactivated")
@bp.get("/restaurants/<rid>/audit-logs")
@jwt_required()
def audit_logs(rid):
 _,f=require_restaurant(rid)
 if f:return f
 try:p=max(1,int(request.args.get("page",1)));s=min(100,max(1,int(request.args.get("pageSize",20))))
 except ValueError:return jsonify(error="validation_error",message="page and pageSize must be integers"),400
 q={"restaurantId":rid};total=mongo.db.audit_logs.count_documents(q);docs=list(mongo.db.audit_logs.find(q).sort("createdAt",-1).skip((p-1)*s).limit(s));return jsonify(items=serialize_doc(docs),total=total,page=p,pageSize=s)
