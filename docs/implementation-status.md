# Implementation status

_Last updated: 2026-11-23_

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| 0. Foundation | Done | FastAPI app, migrations, Unified UI tokens, branded shell, ruff, pytest, CI, README, `.env.example`. |
| 1. Projects and owner access | Done | Supabase magic-link sign-in (**built, not yet tested against a live Supabase project**: no credentials in this environment) plus dev sign-in. Projects, participants, Room PIN, rate limiting, name selection, remembered devices, PIN rotation, device revocation, personal invitations. |
| 2. Photos and parallel assignments | Done | Original upload (immutable) with thumbnail and preview jobs, gallery with state filters, opt-in and capacity (owner- or contributor-set), balanced preview, publish, adjust, manual reassignment that keeps history, "My photos", progress from real states. |
| 3. Uploads and revisions | Done | Download original (recorded), upload validation and metadata, immutable revisions with parent links, notes, history ("My edits"), owner review screen. |
| 4. Smart Merge | Done | Alignment (scale, shift, quarter-turn, crop search), warnings, Low/Medium/High detection, brush and eraser editor (undo/redo, invert, clear, whole edit, zoom/pan/pinch, before/after), manual line-up, full-res lossless composite, queue with retries and failure states. |
| 5. Approval and pilot | Partly done | Approve, ask for changes (note shown to the contributor), final selection, ZIP export, activity history, mobile layout, permission tests, browser E2E. **Not done: a pilot with a real group shoot, real-device phone testing, deployment.** |

## Acceptance criteria (brief §15)

| # | Criterion | Verified by |
|---|---|---|
| 1 | Owner creates a project with a persistent 6-digit PIN | `test_project_has_persistent_six_digit_pin`, E2E |
| 2 | Contributor joins without an email account | `test_contributor_joins_without_email`, E2E |
| 3 | Identity remembered on the device and revocable | `test_contributor_joins_without_email`, `test_owner_can_revoke_remembered_device`, `test_not_me_and_leave` |
| 4 | Contributor cannot access owner functions | `test_contributor_cannot_use_owner_functions`, `test_other_owner_gets_404` |
| 5 | Several contributors edit different photos at once | `test_parallel_editing_by_several_contributors` |
| 6 | Owner previews and adjusts assignments | `test_balanced_distribution_preview_and_publish`, `test_reassignment_preserves_history`, E2E |
| 7 | Download original, upload edit | `test_full_editing_journey`, E2E |
| 8 | New edits never overwrite the original or earlier revisions | `test_new_upload_never_overwrites_original_or_previous`, `test_database_enforces_immutability` |
| 9 | Smart Merge mask from an original and a lower-res JPEG | `test_lowres_jpeg_export_produces_local_mask` |
| 10 | Refine the mask with brush and eraser | E2E brush stroke + undo/redo in the browser; mask upload in `test_full_editing_journey`. The eraser shares the brush code path and is not exercised separately in E2E. |
| 11 | Composite keeps the original canvas size | `test_composite_keeps_canvas_and_untouched_pixels`, `test_full_editing_journey` |
| 12 | Untouched regions keep the original pixels | `test_composite_keeps_canvas_and_untouched_pixels` (exact pixel equality) |
| 13 | Warns on geometric mismatch or unreliable alignment | `test_unrelated_image_is_blocked`, `test_reshaped_region_warns_geometry`, `test_cropped_export_is_located_and_warned`, `test_unaligned_upload_is_blocked_until_lined_up_by_hand` |
| 14 | Reopen own submission and create another revision | `test_full_editing_journey` |
| 15 | Owner reviews, requests changes, approves, selects final | `test_full_editing_journey`, E2E |
| 16 | Progress reflects real states | `test_progress_reflects_real_states` |
| 17 | Main workflow works in a mobile browser | E2E at 390x844 (mobile emulation in headless Edge). **Not yet checked on real iOS/Android devices.** |
| 18 | PIN brute force is rate-limited | `test_pin_brute_force_is_rate_limited`, `test_rate_limit_decays_and_never_locks_out` |
| 19 | All file access authorised server-side | `test_file_access_is_authorised` |
| 20 | Automated tests cover permissions and revision integrity | `tests/test_api.py` (50 tests in total across suites) |

## Known limitations

- **Not deployed.** Needs a host with a persistent disk and a long-running Python process (see handover.md).
- **Supabase Auth** has not been run against a real project yet. Dev sign-in is clearly labelled and refused in production.
- **Storage:** local disk only. The Google Drive adapter is a planned milestone; the interface is in place.
- No project or photo **deletion** (by design, records are immutable). Owners can archive projects and mark photos "not for editing". A retention and deletion process is an open question for client data.
- Smart Merge limits: see [image-processing.md](image-processing.md#limitations) (no warping, no HEIC, upscaled edits stay soft).
- Alternative edits (several contributors on one photo) exist in the data model but not in the UI. One active assignment per photo is enforced.
- Rate limiting and the queue are single-node (SQLite).
- Accessibility: keyboard reachable, visible focus, token contrast, text labels on all status. No formal audit or screen-reader pass yet.
- No notifications (email or Telegram) when work is assigned or reviewed. Owners share the link themselves.

## Next steps

1. Decide hosting and deploy a pilot instance (handover.md).
2. Configure Supabase Auth and test the magic link end to end.
3. Pilot with a real group shoot, on real phones; collect Smart Merge cases that need tuning.
4. Google Drive storage adapter (project-owned folder), if the pilot shows a need.
5. Notifications through the bot fleet (optional, after the pilot).
