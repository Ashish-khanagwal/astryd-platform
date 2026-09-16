"""Seed a local Lumière owner account and editable website defaults."""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from werkzeug.security import generate_password_hash
from app import create_app
from app.extensions import mongo

RID="lumiere-mayfair"
TYPES=["hero","about","featured_menu","gallery","testimonials","offers","location"]

def main():
    app=create_app()
    with app.app_context():
        db=mongo.db
        db.restaurants.update_one({"restaurantId":RID},{"$set":{"restaurantId":RID,"business_id":RID,"slug":"lumiere","ownerUserId":None,"status":"active","domain":None}},upsert=True)
        db.restaurant_users.update_one({"email":"admin@lumiere.com"},{"$set":{"restaurantId":RID,"email":"admin@lumiere.com","name":"Ava Whitfield","passwordHash":generate_password_hash("admin123"),"role":"owner","permissions":{"menu":True,"branding":True,"homepage":True,"media":True,"offers":True,"addons":True,"settings":True,"users":True},"isActive":True,"updatedAt":datetime.utcnow()},"$setOnInsert":{"createdAt":datetime.utcnow()}},upsert=True)
        for n,t in enumerate(TYPES):db.homepage_sections.update_one({"restaurantId":RID,"type":t},{"$setOnInsert":{"restaurantId":RID,"type":t,"order":n,"visible":True,"draftContent":{},"publishedContent":{},"updatedAt":datetime.utcnow()}},upsert=True)
        theme={"restaurantName":"Lumière","themePresetId":"gold","customPrimaryColor":"#C9A24D","primaryFont":"Inter","headingFont":"Playfair Display"}
        db.brand_settings.update_one({"restaurantId":RID},{"$setOnInsert":{"restaurantId":RID,"draft":theme,"published":theme,"updatedAt":datetime.utcnow()}},upsert=True)
        db.website_settings.update_one({"restaurantId":RID},{"$setOnInsert":{"restaurantId":RID,"publishStatus":"draft","publishedAt":None,"seoTitle":"Lumière","seoDescription":""}},upsert=True)
    print("Seed complete")
if __name__=="__main__":main()
