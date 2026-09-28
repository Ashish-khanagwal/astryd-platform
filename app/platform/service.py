"""Atomic tenant provisioning and defaults shared by signup and administration."""

import re
import secrets
from datetime import datetime, timezone

from bson import ObjectId
from werkzeug.security import generate_password_hash

MODULES = ('items', 'catalog', 'booking', 'membership')
DEFAULTS = {
    'restaurant': [('Menu', True), ('Online Order', True), ('Reservations', True), ('Membership', False)],
    'gym': [('Programs', True), ('Shop', False), ('Classes', True), ('Membership', True)],
    'retail': [('Products', True), ('Online Order', True), ('Appointments', False), ('Loyalty', True)],
    'salon': [('Services', True), ('Shop', False), ('Book Now', True), ('Memberships', True)],
    'coffee': [('Menu', True), ('Order Ahead', True), ('Reserve a Table', False), ('Rewards', True)],
}
# Starting layout per module (items, catalog, booking, membership); owners can switch any of them later.
TEMPLATE_VARIANTS = {
    'restaurant': ('a', 'a', 'a', 'a'),
    'gym': ('b', 'b', 'b', 'b'),
    'retail': ('c', 'c', 'c', 'c'),
    'salon': ('a', 'a', 'c', 'c'),
    'coffee': ('a', 'a', 'a', 'a'),
}
THEME_PRESETS = {'dusty-gold', 'emerald', 'burgundy', 'sapphire', 'rosewood', 'espresso'}
PERMISSIONS = ('menu', 'branding', 'homepage', 'media', 'offers', 'addons', 'settings', 'users', 'booking', 'membership')


def now():
    return datetime.now(timezone.utc)


def text(value, field, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f'{field} is required and must be at most {maximum} characters')
    return value.strip()


def email(value):
    value = text(value, 'email', 254).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('A valid email is required')
    return value


def password(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 128 or not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise ValueError('Password must contain 10–128 characters, including letters and numbers')
    return value


def validate_site(body):
    vertical = body.get('vertical')
    if vertical not in DEFAULTS:
        raise ValueError('vertical must be restaurant, gym, retail, salon or coffee')
    slug = text(body.get('slug'), 'slug', 63).lower()
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug) or slug in {'www', 'api', 'admin', 'app', 'login', 'signup'}:
        raise ValueError('Invalid or reserved subdomain')
    name = text(body.get('siteName', body.get('name')), 'siteName')
    branding = body.get('branding') or {}
    if not isinstance(branding, dict) or branding.get('themePresetId', 'dusty-gold') not in THEME_PRESETS:
        raise ValueError('Invalid theme preset')
    modules = body.get('modules')
    if modules is not None:
        if not isinstance(modules, list) or len(modules) != 4 or any(not isinstance(m, dict) for m in modules) or {m.get('module') for m in modules} != set(MODULES):
            raise ValueError('modules must contain each of the four modules exactly once')
        for entry in modules:
            text(entry.get('navLabel'), 'navLabel', 60)
            if not isinstance(entry.get('enabled'), bool):
                raise ValueError('enabled must be a boolean')
    return vertical, slug, name


def insert_site(db, organization_id, owner_id, body, session, platform_domain):
    vertical, slug, name = validate_site(body)
    if db.restaurants.find_one({'slug': slug}, session=session):
        raise ValueError('That subdomain is already taken')
    site_id = 'site_' + secrets.token_hex(12)
    timestamp = now()
    site = {'_id': ObjectId(), 'restaurantId': site_id, 'business_id': site_id,
            'organizationId': organization_id, 'ownerUserId': owner_id, 'slug': slug,
            'name': name, 'vertical': vertical, 'status': 'active', 'brandingBadgeEnabled': True,
            'createdAt': timestamp, 'updatedAt': timestamp}
    db.restaurants.insert_one(site, session=session)
    modules = [{k:m[k] for k in ('module','navLabel','enabled')} for m in body['modules']] if body.get('modules') else [dict(module=m, navLabel=label, enabled=enabled) for m, (label, enabled) in zip(MODULES, DEFAULTS[vertical])]
    variants = dict(zip(MODULES, TEMPLATE_VARIANTS[vertical]))
    db.page_configs.insert_many([{'restaurantId': site_id, **m, 'order': n, 'templateVariant': variants[m['module']],
                                 'published': {**m, 'order': n, 'templateVariant': variants[m['module']]},
                                 'createdAt': timestamp, 'updatedAt': timestamp} for n, m in enumerate(modules)], session=session)
    branding = body.get('branding') or {}
    tagline = branding.get('tagline', '')
    if not isinstance(tagline, str) or len(tagline) > 1000:
        raise ValueError('Invalid tagline')
    brand = {'restaurantId': site_id, 'restaurantName': name, 'tagline': tagline,
             'logoMediaId': None, 'faviconMediaId': None, 'themePresetId': branding.get('themePresetId', 'dusty-gold'),
             'primaryFont': 'Inter', 'headingFont': 'Playfair Display', 'fontWeight': 'regular',
             'buttonStyle': 'rounded', 'borderRadius': 'md', 'socialLinks': {},
             'contact': {'phone': '', 'email': body.get('ownerEmail', ''), 'address': ''},
             'description': tagline, 'cuisineType': '', 'businessHours': [], 'headerVariant': 'a', 'footerVariant': 'a',
             'createdAt': timestamp, 'updatedAt': timestamp}
    db.brand_settings.insert_one({'restaurantId': site_id, 'draft': brand, 'published': brand}, session=session)
    sections = [('hero', {'heading': name, 'description': tagline or 'Welcome to our new site.', 'buttonText': 'Get Started', 'buttonLink': '#/items', 'backgroundMediaId': None, 'overlayOpacity': 40}),
                ('about', {'heading': f'About {name}', 'description': tagline}), ('featured_menu', {}),
                ('gallery', {}), ('testimonials', {}), ('offers', {}), ('location', {})]
    db.homepage_sections.insert_many([{'restaurantId': site_id, 'type': typ, 'order': n,
                                      'visible': typ in {'hero', 'about', 'location'}, 'templateVariant': 'a',
                                      'publishedTemplateVariant': 'a', 'draftContent': content,
                                      'publishedContent': content, 'updatedAt': timestamp} for n, (typ, content) in enumerate(sections)], session=session)
    db.page_content.insert_one({'restaurantId': site_id, 'draft': {}, 'published': {}, 'updatedAt': timestamp}, session=session)
    db.website_settings.insert_one({'restaurantId': site_id, 'publishStatus': 'draft', 'publishedAt': None,
                                   'seoTitle': name, 'seoDescription': tagline}, session=session)
    hours = [{'day_of_week': day, 'open_time': '09:00', 'close_time': '21:00', 'slot_duration_mins': 60,
              'max_per_slot': 12 if vertical == 'gym' else 4, 'is_closed': False, 'disabled_slots': []} for day in range(7)]
    db.businesses.insert_one({'business_id': site_id, 'organizationId': organization_id, 'name': name,
                             'type': vertical, 'operating_hours': hours, 'blocked_dates': [],
                             'created_at': timestamp}, session=session)
    # Reuse the existing capacity/booking engine; each unit is an independently reservable resource.
    db.tables.insert_many([{'business_id': site_id, 'name': f'Resource {i + 1}', 'capacity': 100,
                           'seating_type': seating, 'is_active': True} for seating in ('Indoor', 'Outdoor', 'The Bar', 'Private') for i in range(12 if vertical == 'gym' else 4)], session=session)
    db.domain_mappings.insert_one({'restaurantId': site_id, 'type': 'platform_subdomain',
                                  'hostname': f'{slug}.{platform_domain}' if platform_domain else None,
                                  'verificationStatus': 'pending', 'sslStatus': 'pending',
                                  'createdAt': timestamp, 'updatedAt': timestamp}, session=session)
    return site


def provision(db, body, platform_domain):
    validate_site(body)
    name = text(body.get('organizationName'), 'organizationName')
    owner_name = text(body.get('ownerName'), 'ownerName')
    owner_email = email(body.get('ownerEmail'))
    secret = password(body.get('password'))
    if secret != body.get('passwordConfirmation'):
        raise ValueError('Passwords do not match')
    base = re.sub('[^A-Z0-9]', '', name.upper())[:16] or 'ORG'
    with db.client.start_session() as session:
        def transaction(session):
            code, suffix = base, 2
            while db.organizations.find_one({'code': code}, session=session):
                code, suffix = f'{base}{suffix}', suffix + 1
            organization_id, user_id = str(ObjectId()), ObjectId()
            timestamp = now()
            db.organizations.insert_one({'_id': ObjectId(organization_id), 'code': code, 'name': name,
                                         'status': 'active', 'createdAt': timestamp, 'updatedAt': timestamp}, session=session)
            site = insert_site(db, organization_id, str(user_id), body, session, platform_domain)
            user = {'_id': user_id, 'organizationId': organization_id, 'restaurantId': site['restaurantId'],
                    'siteAccess': 'all', 'email': owner_email, 'normalizedEmail': owner_email,
                    'name': owner_name, 'passwordHash': generate_password_hash(secret), 'role': 'owner',
                    'isActive': True, 'emailVerified': False, 'permissions': {p: True for p in PERMISSIONS},
                    'createdAt': timestamp, 'updatedAt': timestamp}
            db.restaurant_users.insert_one(user, session=session)
            return user, code
        return session.with_transaction(transaction)
