-- Seed data for e2e testing of GET /masjids/nearby payload extension (Gap #2).
-- Reference search point: Dhaka (lat 23.7300, lng 90.4125).
-- Idempotent: clears its own fixed-UUID rows first.

BEGIN;

DELETE FROM masjids WHERE masjid_id IN (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222',
  '33333333-3333-3333-3333-333333333333',
  '44444444-4444-4444-4444-444444444444',
  '55555555-5555-5555-5555-555555555555'
);

-- A: active, near, facilities (parking+sisters+wudu), TWO photos (one cover)
INSERT INTO masjids (masjid_id, name, address, admin_region, location, status, verified, donations_enabled, timezone)
VALUES ('11111111-1111-1111-1111-111111111111', 'Baitul Mukarram', 'Paltan, Dhaka', 'Dhaka',
        ST_GeographyFromText('SRID=4326;POINT(90.4125 23.7300)'), 'active', true, true, 'Asia/Dhaka');
INSERT INTO masjid_facilities (masjid_id, has_sisters_section, has_wudu_area, has_wudu_male, has_wudu_female, has_wheelchair_access, has_parking, has_janazah, has_school)
VALUES ('11111111-1111-1111-1111-111111111111', true, true, true, true, false, true, false, false);
INSERT INTO masjid_photos (photo_id, masjid_id, url, is_cover, display_order)
VALUES (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'https://cdn.example.com/baitul-mukarram-other.jpg', false, 1),
       (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'https://cdn.example.com/baitul-mukarram-cover.jpg', true, 0);

-- B: active, near, facilities (wheelchair+janazah+school), NO photos (cover_photo_url -> null)
INSERT INTO masjids (masjid_id, name, address, admin_region, location, status, verified, donations_enabled, timezone)
VALUES ('22222222-2222-2222-2222-222222222222', 'Gulshan Central Masjid', 'Gulshan, Dhaka', 'Dhaka',
        ST_GeographyFromText('SRID=4326;POINT(90.4193 23.7400)'), 'active', false, false, 'Asia/Dhaka');
INSERT INTO masjid_facilities (masjid_id, has_sisters_section, has_wudu_area, has_wudu_male, has_wudu_female, has_wheelchair_access, has_parking, has_janazah, has_school)
VALUES ('22222222-2222-2222-2222-222222222222', false, false, false, false, true, false, true, true);

-- C: active, near, NO facilities row at all (LEFT JOIN -> all booleans false), no photos
INSERT INTO masjids (masjid_id, name, address, admin_region, location, status, verified, donations_enabled, timezone)
VALUES ('33333333-3333-3333-3333-333333333333', 'Dhanmondi Eidgah Masjid', 'Dhanmondi, Dhaka', 'Dhaka',
        ST_GeographyFromText('SRID=4326;POINT(90.4150 23.7350)'), 'active', false, false, 'Asia/Dhaka');

-- D: active but FAR (Chittagong) -> excluded by 5km radius
INSERT INTO masjids (masjid_id, name, address, admin_region, location, status, verified, donations_enabled, timezone)
VALUES ('44444444-4444-4444-4444-444444444444', 'Chittagong Jame Masjid', 'Chittagong', 'Chittagong',
        ST_GeographyFromText('SRID=4326;POINT(91.7800 22.3500)'), 'active', false, false, 'Asia/Dhaka');

-- E: near but status=pending -> excluded (only ACTIVE returned)
INSERT INTO masjids (masjid_id, name, address, admin_region, location, status, verified, donations_enabled, timezone)
VALUES ('55555555-5555-5555-5555-555555555555', 'Pending Masjid', 'Motijheel, Dhaka', 'Dhaka',
        ST_GeographyFromText('SRID=4326;POINT(90.4130 23.7310)'), 'pending', false, false, 'Asia/Dhaka');

COMMIT;
