; SLink companion overlay -- Polished Crystal v3.2.3 in-game panel (card POL-PANEL, Stage 2).
;
; The handshake is the shared GB one, unchanged: PANEL_STATE / PANEL_PAGE / PANEL_PAGES at
; wSlinkMailbox +9/+10/+11 (patch/gb/slink_abi.inc), AWAIT then STAGED, and A advances while B
; closes. Only the PAYLOAD differs from the vanilla Crystal overlay's panel, and it is the whole
; reason this file exists:
;
; Vanilla's panel is a lease on a graphics buffer. The host paints wTileMap/wAttrmap while the
; screen is hidden and the cartridge reveals it with WaitBGMap2. Polished has no WaitBGMap2
; (docs/polished/PANEL.md E7: "the port's central unknown"), so that port is blocked on a
; transfer primitive nobody has measured. This panel sidesteps it entirely by moving TEXT and not
; pixels: the host writes the page into the mailbox in the game's own charmap and the ROM hands
; it to PrintText. The text engine owns the tilemap (SetUpTextbox -> ClearSpeechBox ->
; ApplyTilemap), so there is no tile transfer, no new graphics tile, no hBGMapMode handshake and
; no attribute map -- and the page is a native text box, with the game's border, palettes, blink
; arrow and per-frame print, which is what "feel vanilla" actually means here.
;
; Wire: wSlinkPanelText (mailbox +34) is SLINK_PANEL_LINES fixed-stride lines of
; SLINK_PANEL_LINE_MAX glyphs, each closed by the game's string terminator (charmap `@`, $53).
; Both lines are space-padded by the host, so a page always fully overwrites the fallback below.
DEF SLINK_PANEL_LINES EQU 2
DEF SLINK_PANEL_LINE_MAX EQU 16
DEF SLINK_PANEL_STRIDE EQU SLINK_PANEL_LINE_MAX + 1
ASSERT SLINK_OFS_PANEL_TEXT + SLINK_PANEL_LINES * SLINK_PANEL_STRIDE <= SLINK_MAILBOX_SIZE
ASSERT wSlinkPanelText == wSlinkMailbox + SLINK_OFS_PANEL_TEXT

SECTION "SLink Panel", ROMX, BANK[SLINK_SERVICE_BANK]

SlinkPanel::
	ldh a, [hInMenu]
	push af
	ld a, TRUE
	ldh [hInMenu], a
	xor a
	ld [wSlinkMailbox + SLINK_OFS_PANEL_STATE], a
	ld [wSlinkMailbox + SLINK_OFS_PANEL_PAGE], a

.page
	; The fallback is printed FIRST and stays up until the host answers, so the screen is never
	; blank, a host that never stages a page leaves a native page behind, and a late host cannot
	; retype text the player has already read. Same lease discipline as the vanilla panel.
	;
	; It deliberately does NOT end in `prompt` (stage-2 live finding: with a prompt here,
	; ButtonSound blocked on a button press BEFORE AWAIT was ever published, so the host was not
	; asked until the player pressed and every page cost two presses). Ending in `done` returns at
	; once, so AWAIT -- the thing the lease exists for -- is the first thing the panel waits on.
	ld hl, SlinkPanelFallback
	call PrintText
	ld a, SLINK_PANEL_AWAIT
	ld [wSlinkMailbox + SLINK_OFS_PANEL_STATE], a
	call .WaitForStage
	; A timeout closes the lease before the fallback is handed over.
	cp SLINK_PANEL_STAGED
	jr nz, .nostage
	; Paint the staged lines over the fallback's two rows, each at ITS OWN origin. (The old chain
	; `<RAM> a <LNBRK> <RAM> b` resumed line 2 at the END of line 1's cursor, so line 2 started 16
	; columns too far right and wrapped past the box edge: live C2/C3 screen, "PAR|TNER: RED".
	; home/text.asm HandleLineBreak advances the string's STARTING coords, not the cursor.) The text box,
	; its border and palette are already up from the fallback's PrintText; both lines are space-padded
	; to exactly SLINK_PANEL_LINE_MAX glyphs so they fully overwrite the fallback. Line 2 sits ONE row below line 1
	; (the old <LNBRK> was NO_LINE_SPACING: one row; the first fix used +2 and left the fallback's `NO CLIENT` on the
	; row in between: live C2 'fallback second line is gone' FAIL).
	; Only native literal bytes may reach PlaceString. Bytes below the native
	; BATTLEEXTRA_GFX_START boundary are commands, ngrams or special dispatch
	; (including NEXT and CTXT), not single tiles. Sanitize all sixteen positions
	; on BOTH rows, including premature terminators, then force the stride's '@'.
	ld hl, wSlinkPanelText
	call .SanitizeLine
	call .SanitizeLine
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY
	ld de, wSlinkPanelText
	rst PlaceString
	hlcoord TEXTBOX_INNERX, TEXTBOX_INNERY + 1   ; the fallback's own line 2 row (next1 = one row down), so it is overwritten
	ld de, wSlinkPanelText + SLINK_PANEL_STRIDE
	rst PlaceString
	; NOT `jr .ready`: the script's trailing <PROMPT> is a Huffman/text TERMINATOR, so PrintText returns
	; without any ButtonSound wait (home/text.asm CheckTerminatorChar returns on <PROMPT> before dispatch),
	; and hJoyDown still holds the A that chose "Call". Reading it at .ready would advance/close the page
	; the same frame it is drawn (live C2/C3 finding). Fall through to .WaitForButton: consume that held
	; key and wait for a FRESH edge, exactly as the no-host path does.
	jr .waitkey
.nostage
	; A timeout closes the lease (vanilla discipline: PANEL_STATE back to CLOSED) so a late host cannot
	; stage a page the player never sees.
	xor a
	ld [wSlinkMailbox + SLINK_OFS_PANEL_STATE], a
.waitkey
	; No ButtonSound ran, so the A that chose "Call" is still an unconsumed edge and would close the
	; panel immediately. Consume it and take a fresh one (the vanilla panel's own discipline).
	call .WaitForButton
.ready
	ldh a, [hJoyDown]
	and PAD_B | PAD_START
	jr nz, .close
	ld a, [wSlinkMailbox + SLINK_OFS_PANEL_STATE]
	cp SLINK_PANEL_STAGED
	jr nz, .close ; no client: A closes too; there is no paging into a fallback
	ldh a, [hJoyDown]
	and PAD_A
	jr z, .close
	ld a, [wSlinkMailbox + SLINK_OFS_PANEL_PAGE]
	inc a
	ld b, a
	ld a, [wSlinkMailbox + SLINK_OFS_PANEL_PAGES]
	cp b
	jr c, .close ; a zero page count is one page, as in the shared GB ABI
	jr z, .close ; A on the last page closes; there is no wrap to the first page
	ld a, b
	ld [wSlinkMailbox + SLINK_OFS_PANEL_PAGE], a
	jp .page

.close
	xor a
	ld [wSlinkMailbox + SLINK_OFS_PANEL_STATE], a
	ld [wSlinkMailbox + SLINK_OFS_PANEL_PAGE], a
	pop af
	ldh [hInMenu], a
	ret

.WaitForStage:
	ld b, 90
.poll
	call DelayFrame
	ld a, [wSlinkMailbox + SLINK_OFS_PANEL_STATE]
	cp SLINK_PANEL_STAGED
	ret z
	dec b
	jr nz, .poll
	ret

; Only reached on the no-host path (.nostage), where no prompt consumed the opening key. This is
; patch/gen2/src/panel.asm's own .WaitForButton, unchanged in behaviour: GetJoypad refreshes
; hJoyDown and JoyTextDelay exposes a new edge in hJoyPressed.
.WaitForButton:
.release
	call DelayFrame
	call JoyTextDelay
	ldh a, [hJoyDown]
	and PAD_A | PAD_B | PAD_START
	jr nz, .release
.press
	call DelayFrame
	call JoyTextDelay
	ldh a, [hJoyPressed]
	and PAD_A | PAD_B | PAD_START
	jr z, .press
	ret

; HL advances exactly one fixed mailbox stride. Literal battle glyphs ($5f+)
; are safe too: native PlaceNextChar writes them as one tile, without dispatch.
.SanitizeLine:
	ld b, SLINK_PANEL_LINE_MAX
.glyph
	ld a, [hl]
	cp BATTLEEXTRA_GFX_START
	jr nc, .literal
	ld a, ' '
	ld [hl], a
.literal
	inc hl
	dec b
	jr nz, .glyph
	ld a, '@'
	ld [hli], a
	ret


; Both strings are exactly SLINK_PANEL_LINE_MAX glyphs, space-padded, so printing the staged
SlinkPanelFallback:
	text "SOUL LINK       "
	next1 "NO CLIENT       "
	done                            ; NOT prompt: see the .page comment -- AWAIT must come first

SlinkPanelEnd::
