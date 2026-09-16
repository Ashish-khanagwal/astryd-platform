"""Restaurant-scoped menu, addon, and offer APIs."""
from datetime import datetime
from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from app.common.api import log_audit_event, require_restaurant, serialize_doc
from app.extensions import mongo

bp = Blueprint("menu", __name__, url_prefix="")
ITEM_FIELDS={"categoryId","name","description","price","imageMediaId","imageUrl","foodType","tags","prepTimeMinutes","rating","reviewCount","isAvailable","isFeatured","variants","addonIds","activeOfferId","displayOrder"}

def oid(v):
    try:return ObjectId(v)
    except Exception:return None
def access(rid):return require_restaurant(rid)
def audit(i,a,t,e,s,d=None):log_audit_event(mongo.db,i["restaurantId"],i["userId"],i["name"],a,t,e,s,d)
def body(fields=None):
    data=request.get_json(silent=True) or {}
    return {k:v for k,v in data.items() if fields is None or k in fields}
def notfound(name):return jsonify(error="not_found",message=f"{name} not found"),404

@bp.get("/restaurants/<rid>/menu")
def menu(rid):
    q={"restaurantId":rid,"deletedAt":None}
    return jsonify(categories=serialize_doc(list(mongo.db.menu_categories.find(q).sort("displayOrder",1))),items=serialize_doc(list(mongo.db.menu_items.find(q).sort("displayOrder",1))))

@bp.post("/restaurants/<rid>/menu/categories")
@jwt_required()
def category_create(rid):
    i,f=access(rid)
    if f:return f
    d=body({"name","description","imageMediaId"})
    if not d.get("name"):return jsonify(error="validation_error",message="name is required"),400
    last=list(mongo.db.menu_categories.find({"restaurantId":rid,"deletedAt":None}).sort("displayOrder",-1).limit(1));now=datetime.utcnow();d.update(restaurantId=rid,displayOrder=(last[0].get("displayOrder",-1)+1 if last else 0),isVisible=True,deletedAt=None,createdAt=now,updatedAt=now);r=mongo.db.menu_categories.insert_one(d);d["_id"]=r.inserted_id;audit(i,"create","menu_category",r.inserted_id,"Created category",d);return jsonify(serialize_doc(d)),201

def category_update(rid,cid,delete=False):
    i,f=access(rid)
    if f:return f
    x=oid(cid);now=datetime.utcnow();updates={"deletedAt":now,"updatedAt":now} if delete else {**body({"name","description","imageMediaId","isVisible","displayOrder"}),"updatedAt":now};d=mongo.db.menu_categories.find_one_and_update({"_id":x,"restaurantId":rid,"deletedAt":None},{"$set":updates},return_document=True)
    if not d:return notfound("Category")
    audit(i,"delete" if delete else "update","menu_category",x,"Deleted category" if delete else "Updated category",updates)
    return jsonify(message="Category deleted") if delete else jsonify(serialize_doc(d))
@bp.put("/restaurants/<rid>/menu/categories/<cid>")
@jwt_required()
def category_put(rid,cid):return category_update(rid,cid)
@bp.delete("/restaurants/<rid>/menu/categories/<cid>")
@jwt_required()
def category_delete(rid,cid):return category_update(rid,cid,True)

def reorder(rid,coll,typ):
    i,f=access(rid)
    if f:return f
    entries=(request.get_json(silent=True) or {}).get("order",[]);now=datetime.utcnow()
    for v in entries:mongo.db[coll].update_one({"_id":oid(v.get("id")),"restaurantId":rid,"deletedAt":None},{"$set":{"displayOrder":v.get("displayOrder"),"updatedAt":now}})
    docs=list(mongo.db[coll].find({"restaurantId":rid,"deletedAt":None}).sort("displayOrder",1));audit(i,"reorder",typ,rid,f"Reordered {typ}s",entries);return jsonify(serialize_doc(docs))
@bp.put("/restaurants/<rid>/menu/categories/order")
@jwt_required()
def category_order(rid):return reorder(rid,"menu_categories","menu_category")

@bp.post("/restaurants/<rid>/menu/items")
@jwt_required()
def item_create(rid):
    i,f=access(rid)
    if f:return f
    d=body(ITEM_FIELDS)
    if not d.get("name") or not d.get("categoryId"):return jsonify(error="validation_error",message="name and categoryId are required"),400
    last=list(mongo.db.menu_items.find({"restaurantId":rid,"deletedAt":None}).sort("displayOrder",-1).limit(1));now=datetime.utcnow();d.update(restaurantId=rid,displayOrder=(last[0].get("displayOrder",-1)+1 if last else 0),deletedAt=None,createdAt=now,updatedAt=now);r=mongo.db.menu_items.insert_one(d);d["_id"]=r.inserted_id;audit(i,"create","menu_item",r.inserted_id,"Created item",d);return jsonify(serialize_doc(d)),201
def item_update(rid,item_id,availability=False,delete=False):
    i,f=access(rid)
    if f:return f
    x=oid(item_id);now=datetime.utcnow();u={"deletedAt":now,"updatedAt":now} if delete else {**body({"isAvailable"} if availability else ITEM_FIELDS),"updatedAt":now};d=mongo.db.menu_items.find_one_and_update({"_id":x,"restaurantId":rid,"deletedAt":None},{"$set":u},return_document=True)
    if not d:return notfound("Item")
    audit(i,"delete" if delete else "update","menu_item",x,"Deleted item" if delete else "Updated item",u);return jsonify(message="Item deleted") if delete else jsonify(serialize_doc(d))
@bp.put("/restaurants/<rid>/menu/items/<item_id>")
@jwt_required()
def item_put(rid,item_id):return item_update(rid,item_id)
@bp.patch("/restaurants/<rid>/menu/items/<item_id>/availability")
@jwt_required()
def item_available(rid,item_id):return item_update(rid,item_id,True)
@bp.delete("/restaurants/<rid>/menu/items/<item_id>")
@jwt_required()
def item_delete(rid,item_id):return item_update(rid,item_id,delete=True)
@bp.put("/restaurants/<rid>/menu/items/order")
@jwt_required()
def item_order(rid):return reorder(rid,"menu_items","menu_item")

def list_entity(rid,coll):
    return jsonify(serialize_doc(list(mongo.db[coll].find({"restaurantId":rid}))))
def write_entity(rid,coll,typ,eid=None,delete=False):
    i,f=access(rid)
    if f:return f
    now=datetime.utcnow()
    if eid is None:
        d=body();d.update(restaurantId=rid,createdAt=now,updatedAt=now);r=mongo.db[coll].insert_one(d);d["_id"]=r.inserted_id;audit(i,"create",typ,r.inserted_id,f"Created {typ}",d);return jsonify(serialize_doc(d)),201
    x=oid(eid)
    if delete:r=mongo.db[coll].delete_one({"_id":x,"restaurantId":rid});d=None
    else:d=mongo.db[coll].find_one_and_update({"_id":x,"restaurantId":rid},{"$set":{**body(),"updatedAt":now}},return_document=True);r=None
    if (delete and not r.deleted_count) or (not delete and not d):return notfound(typ.title())
    audit(i,"delete" if delete else "update",typ,x,f"{'Deleted' if delete else 'Updated'} {typ}");return jsonify(message=f"{typ.title()} deleted") if delete else jsonify(serialize_doc(d))
for coll,typ in (("addons","addon"),("offers","offer")):
    bp.add_url_rule(f"/restaurants/<rid>/{coll}", f"get_{typ}s", lambda rid,c=coll:list_entity(rid,c), methods=["GET"])
    bp.add_url_rule(f"/restaurants/<rid>/{coll}",f"post_{typ}",jwt_required()(lambda rid,c=coll,t=typ:write_entity(rid,c,t)),methods=["POST"])
    bp.add_url_rule(f"/restaurants/<rid>/{coll}/<eid>",f"put_{typ}",jwt_required()(lambda rid,eid,c=coll,t=typ:write_entity(rid,c,t,eid)),methods=["PUT"])
    bp.add_url_rule(f"/restaurants/<rid>/{coll}/<eid>",f"delete_{typ}",jwt_required()(lambda rid,eid,c=coll,t=typ:write_entity(rid,c,t,eid,True)),methods=["DELETE"])
