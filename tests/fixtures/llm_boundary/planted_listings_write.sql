-- Test (d): the planted write. Executed on the fixture store inside the review
-- entry point (or directly, to pin the snapshot), it changes one `listings` row,
-- which the store-unchanged assertion must catch.
UPDATE listings SET class_title = coalesce(class_title, '') || ' (planted)'
WHERE rowid = (SELECT min(rowid) FROM listings)
