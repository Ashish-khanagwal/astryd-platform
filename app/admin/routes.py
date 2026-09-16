from math import ceil
from datetime import datetime
from bson import ObjectId

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import mongo
from app.reservations.service import serialize_reservation
from app.common.api import current_identity, log_audit_event, require_restaurant, serialize_doc
from werkzeug.security import generate_password_hash

bp = Blueprint("admin", __name__, url_prefix="")


@bp.get("/admin/reservations")
@jwt_required()
def list_reservations():
    """List reservations for the business represented by the JWT identity."""
    identity = get_jwt_identity()
    business_id = identity["restaurantId"] if isinstance(identity, dict) else identity
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
        reservations=[serialize_reservation(reservation) for reservation in cursor],
        total=total,
        page=page,
        limit=limit,
        pages=ceil(total / limit) if total else 0,
    )

def _owner(rid): return require_restaurant(rid,{"owner","super_admin"})
def _audit(i,a,t,e,s,d=None):log_audit_event(mongo.db,i["restaurantId"],i["userId"],i["name"],a,t,e,s,d)
@bp.get("/restaurants/<rid>/users")
@jwt_required()
def users(rid):
 _,f=_owner(rid)
 if f:return f
 return jsonify(serialize_doc(list(mongo.db.restaurant_users.find({"restaurantId":rid}))))
@bp.post("/restaurants/<rid>/users")
@jwt_required()
def user_create(rid):
 i,f=_owner(rid)
 if f:return f
 b=request.get_json(silent=True) or {}
 if not all(b.get(k) for k in ("email","name","password")):return jsonify(error="validation_error",message="email, name and password are required"),400
 now=datetime.utcnow();d={"restaurantId":rid,"email":b["email"],"name":b["name"],"passwordHash":generate_password_hash(b["password"]),"role":b.get("role","staff"),"permissions":b.get("permissions",{}),"avatarUrl":b.get("avatarUrl"),"isActive":True,"createdAt":now,"updatedAt":now};r=mongo.db.restaurant_users.insert_one(d);d["_id"]=r.inserted_id;_audit(i,"create","restaurant_user",r.inserted_id,"Created user");return jsonify(serialize_doc(d)),201
@bp.put("/restaurants/<rid>/users/<uid>")
@jwt_required()
def user_put(rid,uid):
 i,f=_owner(rid)
 if f:return f
 b=request.get_json(silent=True) or {};b.pop("restaurantId",None);b.pop("passwordHash",None)
 if "password" in b:b["passwordHash"]=generate_password_hash(b.pop("password"))
 b["updatedAt"]=datetime.utcnow();d=mongo.db.restaurant_users.find_one_and_update({"_id":ObjectId(uid),"restaurantId":rid},{"$set":b},return_document=True)
 if not d:return jsonify(error="not_found",message="User not found"),404
 _audit(i,"update","restaurant_user",uid,"Updated user",b);return jsonify(serialize_doc(d))
@bp.delete("/restaurants/<rid>/users/<uid>")
@jwt_required()
def user_delete(rid,uid):
 i,f=_owner(rid)
 if f:return f
 if uid==i["userId"]:return jsonify(error="invalid_state",message="Cannot deactivate yourself"),400
 r=mongo.db.restaurant_users.update_one({"_id":ObjectId(uid),"restaurantId":rid},{"$set":{"isActive":False,"updatedAt":datetime.utcnow()}})
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
