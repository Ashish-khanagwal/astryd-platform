"""Restaurant homepage, brand, website, and publish APIs."""
from datetime import datetime
from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename
from flask_jwt_extended import jwt_required
from app.common.api import log_audit_event, require_restaurant, serialize_doc
from app.extensions import mongo
bp=Blueprint("businesses",__name__,url_prefix="")
TYPES=["hero","about","featured_menu","gallery","testimonials","offers","location"]
def gate(r):return require_restaurant(r)
def audit(i,a,t,e,s,d=None):log_audit_event(mongo.db,i["restaurantId"],i["userId"],i["name"],a,t,e,s,d)
def ensure(r):
 if not mongo.db.homepage_sections.count_documents({"restaurantId":r}):
  now=datetime.utcnow();mongo.db.homepage_sections.insert_many([{ "restaurantId":r,"type":t,"order":n,"visible":True,"draftContent":{},"publishedContent":{},"updatedAt":now} for n,t in enumerate(TYPES)])
def section(d,v):
 x=serialize_doc(d);x["content"]=x.pop("draftContent" if v=="draft" else "publishedContent",{});x.pop("draftContent",None);x.pop("publishedContent",None);return x
@bp.get("/restaurants/<rid>/homepage")
# @jwt_required()
def homepage(rid):
 v=request.args.get("version","draft")
 if v not in {"draft","published"}:return jsonify(error="validation_error",message="version must be draft or published"),400
 ensure(rid);return jsonify(restaurantId=rid,status=v,sections=[section(d,v) for d in mongo.db.homepage_sections.find({"restaurantId":rid}).sort("order",1)])
@bp.put("/restaurants/<rid>/homepage/sections/order")
@jwt_required()
def section_order(rid):
 i,f=gate(rid)
 if f:return f
 values=(request.get_json(silent=True) or {}).get("order",[]);now=datetime.utcnow()
 for x in values:mongo.db.homepage_sections.update_one({"_id":ObjectId(x["sectionId"]),"restaurantId":rid},{"$set":{"order":x["order"],"updatedAt":now}})
 docs=list(mongo.db.homepage_sections.find({"restaurantId":rid}).sort("order",1));audit(i,"reorder","homepage_section",rid,"Reordered homepage sections",values);return jsonify([section(d,"draft") for d in docs])
@bp.put("/restaurants/<rid>/homepage/sections/<typ>")
@jwt_required()
def section_put(rid,typ):
 i,f=gate(rid)
 if f:return f
 ensure(rid);b=request.get_json(silent=True) or {};u={"updatedAt":datetime.utcnow()}
 if "visible" in b:u["visible"]=b["visible"]
 if "content" in b:u["draftContent"]=b["content"]
 d=mongo.db.homepage_sections.find_one_and_update({"restaurantId":rid,"type":typ},{"$set":u},return_document=True)
 if not d:return jsonify(error="not_found",message="Section not found"),404
 audit(i,"update","homepage_section",d["_id"],"Updated homepage section",u);return jsonify(section(d,"draft"))
def defaults():return {"restaurantName":"Lumière","tagline":"","logoMediaId":None,"faviconMediaId":None,"themePresetId":"gold","customPrimaryColor":"#C9A24D","primaryFont":"Inter","headingFont":"Playfair Display","fontWeight":"400","buttonStyle":"rounded","borderRadius":"8px","socialLinks":{},"contact":{},"description":"","cuisineType":"","businessHours":[]}
@bp.get("/restaurants/<rid>/brand")
# @jwt_required()
def brand_get(rid):
 d=mongo.db.brand_settings.find_one({"restaurantId":rid});return jsonify((d or {}).get(request.args.get("version","draft"),defaults()))
@bp.put("/restaurants/<rid>/brand")
@jwt_required()
def brand_put(rid):
 i,f=gate(rid)
 if f:return f
 b=request.get_json(silent=True) or {};d=mongo.db.brand_settings.find_one_and_update({"restaurantId":rid},{"$set":{"draft":b,"updatedAt":datetime.utcnow()},"$setOnInsert":{"restaurantId":rid,"published":defaults()}},upsert=True,return_document=True);audit(i,"update","brand_settings",d["_id"],"Updated brand settings",b);return jsonify(d["draft"])
@bp.get("/restaurants/<rid>/website")
def web_get(rid):
 return jsonify(serialize_doc(mongo.db.website_settings.find_one({"restaurantId":rid}) or {"restaurantId":rid,"publishStatus":"draft","publishedAt":None,"seoTitle":"","seoDescription":""}))
@bp.put("/restaurants/<rid>/website")
@jwt_required()
def web_put(rid):
 i,f=gate(rid)
 if f:return f
 b={k:v for k,v in (request.get_json(silent=True) or {}).items() if k in {"seoTitle","seoDescription"}};d=mongo.db.website_settings.find_one_and_update({"restaurantId":rid},{"$set":b,"$setOnInsert":{"restaurantId":rid,"publishStatus":"draft","publishedAt":None}},upsert=True,return_document=True);audit(i,"update","website_settings",d["_id"],"Updated website settings",b);return jsonify(serialize_doc(d))
@bp.post("/restaurants/<rid>/publish")
@jwt_required()
def publish(rid):
 i,f=gate(rid)
 if f:return f
 now=datetime.utcnow();ensure(rid)
 for d in mongo.db.homepage_sections.find({"restaurantId":rid}):mongo.db.homepage_sections.update_one({"_id":d["_id"]},{"$set":{"publishedContent":d.get("draftContent",{}),"updatedAt":now}})
 b=mongo.db.brand_settings.find_one({"restaurantId":rid})
 if b:mongo.db.brand_settings.update_one({"_id":b["_id"]},{"$set":{"published":b.get("draft",defaults()),"updatedAt":now}})
 mongo.db.website_settings.update_one({"restaurantId":rid},{"$set":{"publishStatus":"published","publishedAt":now},"$setOnInsert":{"restaurantId":rid}},upsert=True);audit(i,"publish","website",rid,"Published website");return jsonify(publishedAt=now.isoformat(),message="Website published successfully")
@bp.get("/restaurants/<rid>/media")
def media_list(rid):
 try:p=max(1,int(request.args.get("page",1)))
 except ValueError:p=1
 q={"restaurantId":rid}
 if request.args.get("type"):q["fileType"]=request.args["type"]
 if request.args.get("folder"):q["folder"]=request.args["folder"]
 if request.args.get("search"):q["fileName"]={"$regex":request.args["search"],"$options":"i"}
 size=20;total=mongo.db.media_assets.count_documents(q);docs=list(mongo.db.media_assets.find(q).sort("createdAt",-1).skip((p-1)*size).limit(size));return jsonify(items=serialize_doc(docs),total=total,page=p,pageSize=size)
@bp.post("/restaurants/<rid>/media")
@jwt_required()
def media_create(rid):
 i,f=gate(rid)
 if f:return f
 file=request.files.get("file")
 if not file or not file.filename:return jsonify(error="validation_error",message="file is required"),400
 name=f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{ObjectId()}-{secure_filename(file.filename)}";path=__import__("pathlib").Path(current_app.config["UPLOAD_FOLDER"])/name;file.save(path);typ="video" if (file.mimetype or "").startswith("video/") else "image";now=datetime.utcnow();d={"restaurantId":rid,"fileUrl":f"http://localhost:5000/uploads/{name}","thumbnailUrl":None,"fileType":typ,"mimeType":file.mimetype,"fileName":file.filename,"fileSizeBytes":path.stat().st_size,"altText":request.form.get("altText",""),"folder":request.form.get("folder","images"),"createdAt":now,"updatedAt":now};r=mongo.db.media_assets.insert_one(d);d["_id"]=r.inserted_id;audit(i,"create","media_asset",r.inserted_id,"Uploaded media");return jsonify(serialize_doc(d)),201
@bp.delete("/restaurants/<rid>/media/<mid>")
@jwt_required()
def media_delete(rid,mid):
 i,f=gate(rid)
 if f:return f
 d=mongo.db.media_assets.find_one_and_delete({"_id":ObjectId(mid),"restaurantId":rid})
 if not d:return jsonify(error="not_found",message="Media not found"),404
 try:(__import__("pathlib").Path(current_app.config["UPLOAD_FOLDER"])/d["fileUrl"].rsplit("/",1)[-1]).unlink(missing_ok=True)
 except OSError:current_app.logger.exception("Unable to remove media file")
 audit(i,"delete","media_asset",mid,"Deleted media");return jsonify(message="Media deleted")
