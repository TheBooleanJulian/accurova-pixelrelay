# Product requirements (original build brief)

> The brief PixelRelay was built from, kept verbatim. Where the build departs from it, see [decisions](decisions/README.md).

You are the lead product engineer, UX designer, and software architect responsible for creating **PixelRelay**, a new Accurova product.

Your task is to create a working, deployable repository—not merely a proposal, mockup, or implementation plan. Build incrementally, verify each milestone, and keep the product deliberately simple.

## 1. Product identity

**Product:** PixelRelay  
**Parent brand:** Accurova  
**Public identity:** PixelRelay by Accurova  
**Category:** Collaborative photo-editing coordination and revision management  
**Primary tagline:** Every edit moves the picture forward.  
**Supporting line:** Collaborate. Create. Preserve.

PixelRelay helps photographers and cosplayers coordinate collaborative editing of group photographs. It replaces fragmented Telegram coordination and shared-folder confusion with a simple, photo-centric workspace.

The initial target community is cosplay group photography, but do not hard-code cosplay-specific concepts into the product. The product should also work for photography clubs, portrait collaborations, event groups, and other creative teams.

### Core product promise

- Multiple cosplayers edit different assigned photographs at the same time.
- Each contributor has a clear, small list of tasks.
- Original photographs and every submitted edit are preserved.
- Contributors can create a high-resolution composite from their edit using Smart Merge or Manual Brush masking.
- Photographers and organisers can oversee progress and approve final results.

### Product philosophy

**Hide complexity. Make the next action obvious.**

Most cosplayers are not technically savvy and may only participate in one project. The experience must be mobile-first, understandable without training, and require as little account setup as possible.

Do not turn this into generic project-management software, a professional DAM, a photographer CRM, or a social network.

## 2. Scope boundaries

### In scope for the first release

1. Project creation and management.
2. Participant invitations and lightweight access.
3. Importing a set of original photographs.
4. Participant opt-in and assignment of photographs.
5. Parallel editing assignments.
6. Simple download, external edit, upload workflow.
7. Revision history.
8. Smart Merge using automatic image-difference masking.
9. Manual Brush masking and mask refinement.
10. High-resolution compositing onto the original.
11. Project progress and photographer review.
12. Final selection and export.

### Explicitly out of scope for the first release

- Photographer RAW processing or culling workflows.
- Camera tethering or capture workflows.
- AI retouching or generative image editing.
- Automatic face recognition or identity inference.
- Public portfolios, social feeds, community discovery or messaging.
- Complex task boards, calendars or Gantt charts.
- Full Lightroom or Photoshop replacement.
- Automatic pixel-level merging of multiple contributors' edits.
- Automatic synchronisation across several cloud providers.
- LuxSync integration beyond leaving a clean future integration boundary.
- Billing, subscriptions and marketplace features.

Do not add out-of-scope functionality just because it is technically interesting.

## 3. User roles and permissions

Keep roles simple.

### Project owner / photographer / organiser

A project owner can:
- Create and configure a project.
- Add or remove participants.
- Import original photographs.
- Select which photographs are available for editing.
- Set assignment limits and deadlines.
- Distribute or manually reassign photographs.
- View project-wide progress.
- Review submitted composites.
- Approve or request changes.
- Select final versions.
- Rotate the Room PIN and revoke contributor sessions.

For the first release, one owner role is sufficient. Structure permissions so a separate organiser role can be introduced later without major redesign.

### Cosplayer / contributor

A contributor can:
- Join a project.
- Identify themselves as a participant.
- See their own assigned photographs.
- Download original photographs.
- Upload their edited export.
- Use Smart Merge or Manual Brush.
- Preview and adjust their composite.
- Submit a revision.
- View their own submission history and project progress.

Contributors must not be able to change project settings, reassign other people, delete originals, approve their own work as the project final, or access administrative controls.

### Guest viewer

A guest viewer may only see content explicitly made available to them. Do not expose private project content merely because someone has a project URL.

## 4. Authentication and room access

A central product goal is to avoid forcing every cosplayer to create an email account.

Implement **Room PIN access** with a six-digit, cryptographically random PIN for each project.

### Room PIN behaviour

- Generate a random six-digit PIN when a project is created.
- The PIN is persistent until the owner rotates it.
- Display the PIN and a shareable project URL in the owner interface.
- A participant can open the project link and enter the PIN.
- The PIN grants access to that project only.
- The PIN does not grant owner or administrator privileges.
- Provide rate limiting, progressive delays and suspicious-attempt monitoring.
- Avoid permanent lockouts that an attacker could use to deny access to legitimate participants.
- Allow the owner to rotate the PIN and invalidate access tokens issued through the old PIN where appropriate.

A six-digit PIN has low entropy. Treat it as a convenient room-entry secret, not strong identity authentication. Never claim it is more secure than conventional authentication.

### Contributor identity

A shared PIN does not identify an individual. After entering it, the user must select their name from the project's participant list or use an individual invitation link.

For first-release simplicity:
- The owner creates or imports the participant list.
- The contributor selects their name.
- The system creates a project-scoped contributor session.
- The session remembers the contributor on that device.
- Provide a clear “This isn't me” / switch participant option.
- Provide a way to revoke a remembered device/session.
- Do not allow users to impersonate the owner by selecting a name.

For stronger attribution, support individual invitation tokens as an optional path. An invitation token should be random, scoped to one project and one participant, revocable, and preferably single-use for initial identity binding.

### Session security

- Use secure, HttpOnly, SameSite cookies for sessions.
- Never store session secrets in localStorage.
- Use short-lived access credentials with safe renewal or server-side sessions.
- Store only hashed or keyed-hash forms of low-entropy PINs; use a server-side secret pepper where appropriate.
- Do not log PINs, invitation tokens, session cookies or other secrets.
- Protect all project data and file operations with server-side authorisation checks.
- Do not rely on hidden UI controls as access control.
- Provide logout and session revocation.
- Add CSRF protection where relevant to the chosen architecture.
- Validate all inputs server-side.

Owner authentication must be stronger than the shared Room PIN. Implement owner accounts using Supabase Auth (email magic link or passwordless authentication is preferred for the initial release). Keep the owner login separate from contributor room access.

## 5. Core user journeys

### Journey A: Owner creates a project

1. Owner signs in.
2. Creates a project with a name and optional shoot date/description.
3. Adds participants by display name.
4. Imports original photographs.
5. Selects which photographs are available for editing.
6. Sets the number of photographs each participant is willing to edit, or lets participants opt in.
7. Uses automatic distribution or manually adjusts assignments.
8. Shares the project URL and Room PIN through Telegram, WhatsApp or Discord.

The project creation flow should be short and guided. Avoid a large configuration form.

### Journey B: Contributor joins

1. Contributor opens the project link.
2. Enters the six-digit Room PIN or opens an individual invitation link.
3. Selects their participant identity if not already identified.
4. Sees a simple “Your photos” screen.
5. Opens an assigned photograph.
6. Downloads the original.
7. Edits in their preferred external application.
8. Uploads the edited export.
9. Chooses Smart Merge or Manual Brush.
10. Reviews the result and submits it.
11. Sees a clear confirmation and moves to the next photograph.

Do not require contributors to learn version-control terminology.

### Journey C: Owner oversees the project

1. Owner sees all photographs and contributors.
2. Sees which photographs are unassigned, in progress, submitted or awaiting review.
3. Opens a photograph to inspect its revisions and composite.
4. Approves a submission or requests changes.
5. Selects the preferred final version.
6. Exports approved high-resolution composites.

## 6. Parallel assignment system

Parallel editing is a core differentiator.

Do not implement a sequential queue where one cosplayer must finish before another can begin.

### Assignment rules

- Each photograph has one active editing owner at a time in V1.
- Different photographs can be assigned to different contributors and edited simultaneously.
- Each contributor can have multiple assigned photographs.
- Contributors may opt in and state how many photographs they can handle.
- Owners can use automatic distribution or manually assign photographs.
- Unclaimed photographs remain visibly unassigned.
- Owners can reassign work.
- Reassignment must preserve existing uploads and revision history.
- Do not silently replace a contributor's work when an assignment changes.

### Automatic distribution

Implement a deterministic, explainable round-robin or balanced distribution algorithm.

Inputs:
- Eligible photographs.
- Participating contributors.
- Optional per-contributor capacity.
- Optional priority ordering.

Requirements:
- Respect capacity limits.
- Distribute work as evenly as practical.
- Do not assign to people who have not opted in.
- Allow the owner to preview the allocation before publishing it.
- Allow manual adjustment before and after publishing.
- Explain that balanced photo counts do not necessarily mean equal editing effort.

Avoid complex predictive workload algorithms in V1.

### Progress

Calculate progress from actual assignment and submission states, not manually entered percentages.

Suggested states:
- Unassigned
- Assigned
- Downloaded (optional if reliably measurable)
- Editing
- Submitted
- Needs changes
- Approved
- Final selected

Do not claim that a file has been downloaded or opened unless the system can reliably observe that event. Manual status updates are acceptable where automation is uncertain.

## 7. Photo and revision model

The original photograph must be immutable.

For every photo, retain distinct assets:

1. Original — photographer's untouched source.
2. Contributor upload — the exact exported file supplied by the cosplayer.
3. Mask — the saved mask data used for compositing.
4. Composite — the high-resolution output generated from the original and contributor upload.
5. Approval/final reference — the version selected by the owner.

Never overwrite the original, contributor upload, prior mask, or previous composite.

### Revision history

Each submission should record:
- Project and photo IDs.
- Contributor identity.
- Upload timestamp.
- Original upload file reference.
- Original upload dimensions and format.
- Mask data and merge settings.
- Composite file reference.
- Composite dimensions and format.
- Parent revision where applicable.
- Submission status.
- Optional contributor note.
- Processing warnings and validation results.

Use immutable revision records. A new submission creates a new revision.

Do not expose Git terminology to users. Use:
- Original
- Your edit
- Previous edit
- Latest edit
- Final version

A contributor can reopen their own submission, adjust the mask and create a new revision without deleting the previous one.

### Multiple contributors

Different contributors may independently create edits from the same original if the owner chooses to allow it. These are alternatives, not edits that should be automatically merged together.

In V1, default to one active assignment owner per photo. The data model should still support alternative submissions later.

## 8. Smart Merge — core image-processing feature

Smart Merge lets the contributor transfer the changes in their exported image onto the photographer's original high-resolution image.

This addresses contributors unintentionally exporting at lower resolution or with compression.

### Key principle

The contributor should continue using their preferred editing application. PixelRelay handles the comparison, mask, preview and high-resolution compositing after upload.

### Smart Merge workflow

1. Load the original and contributor upload.
2. Validate dimensions, orientation, colour profile and file type.
3. Align the contributor image to the original coordinate system.
4. Detect differences.
5. Generate a grayscale mask.
6. Let the contributor inspect and refine the mask.
7. Composite selected edited regions onto the original-resolution canvas.
8. Preview the result.
9. Save the composite as a new immutable revision.

### Image alignment

Do not assume the two images have identical dimensions or framing.

Support:
- Different resolutions with matching aspect ratios.
- EXIF orientation normalisation.
- Basic scaling and alignment.
- Detection of likely cropping, rotation or aspect-ratio changes.
- Warnings when registration is unreliable.

Do not silently create a misleading composite if alignment fails. Ask the contributor to use manual alignment controls or upload a better-matched export.

For V1, implement basic registration and clear warnings. More advanced geometric warping can be deferred.

### Automatic difference mask

Raw pixel subtraction alone is not robust enough because JPEG compression, resizing and colour-profile differences can produce widespread false positives.

Build a modular detection pipeline that can combine:
- Absolute pixel difference.
- Perceptual colour difference, such as Lab ΔE.
- Structural similarity or equivalent structural comparison.
- Blur/scale-aware comparison to reduce compression noise.
- Morphological operations to remove isolated noise and smooth mask regions.

Expose simple user-facing sensitivity settings:
- Low — retain only obvious changes.
- Medium — balanced detection (default).
- High — detect subtle changes.

Do not expose a large collection of technical thresholds to ordinary users. Keep detailed parameters in an advanced/debug panel for development.

The generated mask should be grayscale:
- White = use contributor edit.
- Black = retain original.
- Grey = blend the two.

### Manual Brush

Provide a simple interactive mask editor:
- Brush to include contributor edit.
- Eraser to restore original.
- Adjustable brush size.
- Adjustable feather/softness.
- Undo and redo.
- Invert mask.
- Reset mask.
- Overlay opacity.
- Zoom and pan.
- Before/after preview.

Make the distinction obvious: painting adds the contributor's edit; erasing restores the original.

Support touch and stylus interaction on mobile/tablet, but prioritise desktop precision for large photographs.

### Hybrid workflow

The normal recommendation should be Smart Merge followed by optional brush refinement.

Allow switching between Smart and Manual modes without losing the existing mask.

### High-resolution compositing

Let:
- B = original high-resolution base.
- E = aligned contributor edit, resampled into base coordinates.
- M = mask with values between 0 and 1.
- F = final composite.

Use alpha compositing:

F(x,y) = M(x,y) * E(x,y) + (1 - M(x,y)) * B(x,y)

The final canvas must use the original photograph's dimensions.

Important limitations:
- Upscaling a smaller edit does not restore lost detail.
- Untouched regions should retain original pixels.
- Edited regions may contain less detail than the original.
- Detail-preservation methods may be explored for suitable edits, but must not be represented as guaranteed restoration.
- Face/body reshaping, major geometry changes, cropping and repositioning require special handling and may not be safely transferable through a simple mask.
- Warn the user when the edit appears geometrically inconsistent.
- Never claim the result is fully restored original quality.

For V1, focus on reliable colour/texture/local-retouch transfer and transparent warnings. Do not attempt AI reconstruction.

### Processing architecture

Do not process full-resolution images entirely in the browser.

Recommended split:
- Browser: interactive preview, zoom/pan, brush interaction and mask editing.
- Server/background worker: image validation, alignment, full-resolution difference generation and final compositing.
- Persist mask data so work can be resumed.

Use a queue or background-job abstraction for large images. Provide processing states and retry handling. Avoid blocking the web request during expensive processing.

Use suitable image-processing libraries such as OpenCV, NumPy, scikit-image/SSIM and libvips/Pillow where appropriate. Choose a practical combination after checking compatibility and deployment constraints. Do not add libraries without a clear purpose.

## 9. Photographer review and approval

The photographer/owner should have a compact project overview.

Required capabilities:
- View all photographs.
- Filter by assignment and status.
- See contributor and revision history.
- Preview submitted composites.
- Compare original and composite.
- Approve a revision.
- Request changes with a short note.
- Select the final version for each photograph.
- Export selected final images.

The photographer does not need to create or refine the contributor's Smart Merge mask by default. The contributor owns their own merge because they know what they changed.

Do not build a complex annotation system in V1.

## 10. UI and visual identity

### Brand

**Accurova PixelRelay**  
Public-facing name: **PixelRelay by Accurova**

Primary tagline:
**Every edit moves the picture forward.**

Supporting tagline:
**Collaborate. Create. Preserve.**

Brand personality:
- Modern
- Approachable
- Precise
- Collaborative
- Trustworthy
- Photography-oriented

Do not make the product look like an anime/cosplay fan site. Cosplay is the initial validation community, not the limit of the product.

### Visual direction

Use a dark photographic interface with restrained cyan accents.

Suggested design tokens:

- Midnight Navy: `#101B2A`
- Deep Ocean: `#173E55`
- Relay Cyan: `#25BFCB`
- Signal Cyan: `#53DDE5`
- Light Neutral: `#E8F2F5`
- Muted Neutral: `#9BAFBD`
- Dark Surface: `#334455`
- Success: `#35B981`
- Warning: `#E5A94C`
- Error: `#E16D72`

Cyan is an accent, not a background for every component. Photography must remain the visual focus.

Typography:
- Manrope for headings.
- Inter for body text and controls.

Logo direction:
- Geometric pixel handoff / connected pixel clusters.
- A compact P monogram may be used for the app icon.
- Must work in monochrome, light and dark contexts, and at small sizes.
- Do not spend the initial implementation milestone polishing a final logo; create a clean text/monogram placeholder and keep the branding replaceable.

### UX requirements

- Mobile-first responsive design.
- Large touch targets.
- Short labels and plain language.
- Minimal navigation.
- No unnecessary dashboards for contributors.
- Avoid technical terms such as commit, branch, repository, mask compositing or asset ingestion in ordinary user-facing UI.
- Provide clear empty, loading, error and success states.
- Keep the next action obvious on every screen.
- Make upload and processing progress visible.
- Support keyboard navigation and accessible contrast.

### Suggested navigation

Contributor:
- Projects
- My Photos
- My Edits

Owner:
- Project Overview
- Photos
- Participants
- Assignments
- Review

Do not show owner navigation to contributors.

### Preferred user-facing terminology

| Technical/internal | User-facing |
|---|---|
| Repository | Project |
| Commit revision | Submit edit |
| Branch | Alternative edit |
| Mask compositing | Smart Merge |
| Asset ingestion | Add photographs |
| Synchronisation failure | Couldn't update |
| Finalise composite | Save final edit |

## 11. Recommended technology

Use a conventional, maintainable stack.

Preferred starting point:
- Next.js with TypeScript.
- App Router.
- Tailwind CSS.
- shadcn/ui.
- Supabase PostgreSQL.
- Supabase Auth for owners.
- Supabase Realtime where useful.
- Server-side API routes/server actions with strict validation.
- A separate background image-processing worker.
- Vercel for the web application, subject to worker deployment requirements.

### File storage

The initial product should support a project-level external storage integration where practical, but do not let Google Drive OAuth complexity block the first working vertical slice.

Recommended implementation sequence:
1. Define a storage-provider abstraction.
2. Implement a reliable initial storage adapter.
3. Prefer project-owned/shared storage rather than files tied to a contributor's personal account.
4. Keep original, upload, mask and composite references in the database.
5. Add Google Drive integration as a focused milestone.
6. Do not build multi-provider synchronisation in V1.

If using Supabase Storage for the first vertical slice, keep it behind the storage abstraction so a Drive adapter can be added later. Clearly document storage limits, file-size limits and deployment assumptions.

### Data model

Start with these core entities:

- users
- projects
- project_members
- contributor_sessions
- room_access_secrets
- invitation_tokens
- photos
- assignments
- revisions
- masks
- approvals
- activity_events
- storage_integrations

Use UUIDs for internal IDs. Use explicit foreign keys, indexes, constraints and timestamps. Apply row-level security where appropriate, but also enforce permissions in server-side application logic.

Suggested relationships:

Project
- has many members
- has many photos
- has one Room PIN secret
- has many activity events

Photo
- belongs to a project
- has one immutable original
- has many assignments over time
- has many revisions
- has one or more approval records

Assignment
- belongs to a photo
- belongs to a contributor
- records status and capacity context

Revision
- belongs to a photo
- belongs to a contributor
- references uploaded edit, mask and composite
- can reference a parent revision
- is immutable after submission except for administrative metadata/status changes

Mask
- belongs to a revision or draft merge session
- stores a reproducible mask asset or compact representation
- stores settings and alignment metadata

Do not store large image binaries in PostgreSQL.

## 12. Engineering standards

- TypeScript strict mode.
- Reusable components and clear module boundaries.
- Database migrations committed to the repository.
- Input validation with a schema library such as Zod.
- Centralised authorisation helpers.
- No secrets committed to source control.
- `.env.example` with documented variables.
- Structured server-side logging with secrets and personal data redacted.
- Error boundaries and useful error messages.
- Unit tests for business logic.
- Integration tests for authentication, permissions, assignments and revision persistence.
- Image-processing tests using small synthetic fixtures.
- End-to-end tests for the main owner and contributor journeys.
- Responsive and accessibility checks.
- README with local setup, architecture, scripts and deployment instructions.

Do not use mock data as a substitute for working functionality once a feature is claimed as implemented. Clearly label any remaining demo-only functionality.

## 13. Repository structure

Use a clean monorepo or single Next.js application with a separate worker package if required.

A suggested structure:

```text
accurova-pixelrelay/
├── apps/
│   ├── web/
│   └── image-worker/
├── packages/
│   ├── database/
│   ├── shared/
│   ├── image-processing/
│   └── ui/
├── supabase/
│   ├── migrations/
│   └── seed.sql
├── docs/
│   ├── product-requirements.md
│   ├── architecture.md
│   ├── security.md
│   ├── image-processing.md
│   └── decisions/
├── tests/
├── .env.example
├── README.md
└── package.json
```

Adjust this structure if a simpler architecture is more maintainable. Do not create empty packages solely to match the example.

## 14. Development milestones

Build in verified vertical slices. Do not attempt the entire product in one giant implementation.

### Milestone 0 — Repository foundation

- Inspect the existing working directory before changing anything.
- Create the repository if one does not exist.
- Initialise the chosen stack.
- Configure linting, formatting, type checking and tests.
- Add README, environment template and architecture notes.
- Establish design tokens and basic responsive layout.
- Create a working branded shell.

Deliverable: app runs locally and checks pass.

### Milestone 1 — Projects and owner access

- Owner authentication.
- Project creation.
- Participant management.
- Six-digit Room PIN generation and storage.
- PIN entry and rate limiting.
- Contributor identity selection.
- Secure remembered sessions.
- Owner PIN rotation and session revocation.

Deliverable: owner can create a project and a contributor can join without email registration.

### Milestone 2 — Photos and parallel assignments

- Add/import photographs.
- Original-file storage.
- Photo gallery.
- Participant opt-in and capacity.
- Automatic balanced assignment.
- Manual reassignment.
- Contributor “My Photos” page.
- Progress states.

Deliverable: several contributors can receive different photographs and work independently.

### Milestone 3 — Uploads and revision history

- Download original.
- Upload contributor edit.
- File validation and metadata extraction.
- Immutable revision records.
- Submission status.
- Contributor edit history.
- Owner review screen.

Deliverable: end-to-end editing workflow works without Smart Merge.

### Milestone 4 — Smart Merge

- Image alignment and validation.
- Difference detection.
- Sensitivity controls.
- Mask generation.
- Manual brush editor.
- Undo/redo and mask persistence.
- Preview compositing.
- Full-resolution server-side composite.
- Processing queue and failure handling.
- Warnings for resolution loss and geometric mismatch.

Deliverable: contributor can create and submit a high-resolution composite.

### Milestone 5 — Approval and real-world pilot

- Owner approval and change requests.
- Final selection.
- High-resolution export.
- Activity history.
- Mobile usability pass.
- Security and permission review.
- End-to-end tests.
- Pilot with a real group shoot and several contributors.

Deliverable: a complete usable MVP.

## 15. Acceptance criteria

The first release is not complete until these scenarios work:

1. An owner can create a project and receive a persistent six-digit Room PIN.
2. A contributor can join without an email account.
3. A contributor's identity is remembered on their device and can be revoked.
4. A contributor cannot access owner functions.
5. Several contributors can be assigned different photographs and edit simultaneously.
6. An owner can preview and adjust assignments.
7. A contributor can download the original and upload an edited export.
8. Uploading a new edit never overwrites the original or a previous revision.
9. Smart Merge can generate a mask from an original and a lower-resolution JPEG export.
10. A contributor can refine the mask using a brush and eraser.
11. The final composite retains the original canvas dimensions.
12. Untouched regions retain original pixels, subject to documented colour/orientation processing.
13. The application warns when the edit is too geometrically different or alignment is unreliable.
14. A contributor can reopen their own submission and create another revision.
15. An owner can review, request changes, approve and select a final composite.
16. Project progress reflects actual assignment and submission states.
17. The main workflow works on a mobile browser.
18. PIN brute-force attempts are rate-limited.
19. All file access is authorised server-side.
20. Automated tests cover critical permissions and revision integrity.

## 16. How you should work

Start by inspecting the repository and the available runtime. Then:

1. Summarise the implementation approach briefly.
2. Identify any genuine blockers or decisions that cannot safely be assumed.
3. Create the repository foundation.
4. Implement Milestone 0 and verify it.
5. Continue through milestones in order, preserving a working application.
6. Do not stop after generating a plan or a set of mock screens.
7. Keep a concise `docs/implementation-status.md` updated with completed work, current work, known limitations and next steps.
8. Run lint, type checks and relevant tests after each milestone.
9. Fix failures before proceeding.
10. If a third-party credential is unavailable, build the integration boundary and a clearly labelled local development adapter; do not pretend the live integration works.
11. Make sensible, reversible decisions without repeatedly asking for approval.
12. Ask only when a decision affects security, data loss, irreversible external actions or a genuinely blocked implementation.

### Final instruction

Prioritise a small, reliable, genuinely usable PixelRelay over feature breadth.

The most important experience is:

**Open link → Enter PIN → See assigned photos → Download → Edit → Upload → Smart Merge → Submit.**

Everything else exists to make that journey reliable, collaborative and safe.
