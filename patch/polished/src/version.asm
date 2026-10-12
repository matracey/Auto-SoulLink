; TITLE-VERSION for Polished Crystal (docs/polished/TITLE.md 8.4): the patch version on the
; New Game / Continue menu, printed ONCE PER PASS of MainMenuJoypadLoop's .loop.
;
; Why per-pass and not once at SetUpMenu: with a save present and the RTC unset, SpeechTextbox rows
; 12-17 are redrawn every pass, so a one-time print is erased (TITLE.md 8.3). The hook is a SAME-SIZE
; rewrite of the 3-byte `call MainMenu_PrintCurrentTimeAndDay` (main_menu.asm:107, flat 0x483CD,
; bytes cd ed 43) to `call SlinkMainMenuLoopBridge`.
;
; The bridge calls the original first, then re-arms hBGMapMode around our print. Per TITLE.md's
; coordinator correction that pair is HYGIENE, not visibility: UpdateBGMap (home/video.asm:148-166)
; treats hBGMapMode 1 as "copy wTilemap to VRAM in halves each VBlank". The wrapper only avoids a
; one-frame tear against the clock text.
;
; Space: ROM0[$3F92] -- AFTER the phone bridge ($3F34..$3F8D), the last free ROM0 gap.
; NOT $0089: the integration merge gave patch/polished/src/slink_sfx.asm its own
; `SECTION "Slink Sound Reset", ROM0[$0089]`, so $0089 is taken and rgblink aborts with
; "section overlaps". Measured free by whole-ROM diff against the clean ROM; rgblink fails closed
; if anything else claims it.
INCLUDE "engine/slink/slink_version.inc"

ASSERT DEF(SLINK_BUILD_VERSION), "the builder's --version defines SLINK_BUILD_VERSION"

SECTION "SLink Main Menu Bridge", ROM0[$3F92]
SlinkMainMenuLoopBridge::
	call MainMenu_PrintCurrentTimeAndDay
	; The original returns with hBGMapMode already 1 (main_menu.asm:141-142), so the tilemap has
	; been flushed; drop to 0 across our print, then re-arm.
	xor a
	ldh [hBGMapMode], a
	farcall SlinkPrintVersion
	ld a, 1
	ldh [hBGMapMode], a
	ret
SlinkMainMenuLoopBridgeEnd::

SECTION "SLink Version", ROMX, BANK[SLINK_SERVICE_BANK]
SlinkPrintVersion:
	hlcoord 1, 10
	; `.text` is a LOCAL of SlinkVersionText (the non-local label that opens its scope), so from
	; SlinkPrintVersion it is out of scope -- name the exported symbol instead.
	ld de, SlinkVersionText
	jp SlinkPrintVersionBounded
SlinkVersionText::
; `.text` and `.end` are LOCAL labels of the non-local label above: a non-local label inserted
; BETWEEN them would close that scope and `.end` would stop resolving (`Expected constant
; expression: .end is not constant`, rgbasm). So the exported label goes first, not in the middle.
.text
	; One literal tile per character is required both by the bounded copier
	; and rom_identity.PREFIX = 9. Default Polished text compresses "ou"
	; and "in" into ngrams; do not let those tokens enter this stamped field.
	pushc
	setcharmap no_ngrams
	db "SoulLink {SLINK_BUILD_VERSION}@"
	popc
.end
	; A FIXED-WIDTH field (patch/tools/rom_identity.py FIELD = 20): stamping a release version changes
	; only these bytes, so the build's canonical identity (field and global checksum masked) never
	; moves -- the same contract patch/gen2/src/version.asm uses.
	ds 20 - (.end - .text), 0
; the ASSERTs come BEFORE any further non-local label, for the same local-scope reason
ASSERT .end - .text - 1 <= SCREEN_WIDTH - 1
ASSERT .end - .text <= 20
SlinkVersionFieldEnd::

SlinkPrintVersionBounded:
	; The native main menu ends at column 16, not SCREEN_WIDTH - 1.
	; Use the active rectangle (also respected by MenuBox) and leave both
	; border columns untouched. Row 10 remains the per-pass version row.
	; Stamped fields contain literal font tiles only, terminated by "@".
	; Copy those glyphs directly; truncate rather than wrap into a border.
	ld a, [wMenuBorderLeftCoord]
	ld c, a
	ld b, 0
	add hl, bc
	ld a, [wMenuBorderRightCoord]
	sub c
	ret c
	ret z
	dec a
	ret z
	ld b, a
.glyph
	ld a, [de]
	cp "@"
	ret z
	ld [hli], a
	inc de
	dec b
	jr nz, .glyph
	ret
SlinkVersionPrinterEnd::
