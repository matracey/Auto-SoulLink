# Move a Radical Red save from mGBA to BizHawk

Auto-SoulLink runs in BizHawk, but an existing Radical Red playthrough can continue there. Transfer the cartridge battery save, not an emulator savestate.

> [!WARNING]
> mGBA savestates such as `.ss0` through `.ss9` are emulator-specific and do not load in BizHawk. Loading an older mGBA savestate after the final in-game save can also restore older cartridge state and overwrite newer progress.

## Understand mGBA's save menu

Leave **File → Save games → Automatically determine** selected for ordinary single-player use.

- **Load alternate save game…** selects another `.sav` as this ROM's persistent read/write battery file, resets the game, and writes future in-game saves back to that selected file.
- **Load temporary save game…** reads another `.sav` for the current session without writing changes back to the selected source.
- **Convert save game…** converts formats; it is not a slot manager.
- **Import/Export GameShark Save…** handles GameShark formats and is not the raw battery-save path used for this migration.
- **Use player 1/2/3/4 save game** selects multiplayer identities for multiple mGBA windows using the same ROM. Player 1 uses `.sav`; players 2 through 4 use `.sa2`, `.sa3`, and `.sa4`. These are parallel files, not rolling backups.

Radical Red's in-game warning that a save already exists and asks whether to overwrite it is normal. The cartridge exposes one playthrough at a time and alternates internal sectors for recovery.

## Create the migration save

1. In mGBA, load the newest intended progress. If the overlay says `State N loaded`, verify the trainer, badges, party and location before continuing.
2. Save through Radical Red's in-game menu and confirm the ordinary overwrite prompt.
3. Close mGBA completely so it flushes battery and RTC data.
4. Find the active player-1 `.sav`. With default mGBA paths it normally sits beside the ROM or in the configured save-game directory.
5. Copy the `.sav` to a separate backup directory before changing anything.
6. Verify the source ROM and save:

   ```powershell
   Get-FileHash -Algorithm MD5 -LiteralPath '.\Pokemon Radical Red.gba'
   Get-Item -LiteralPath '.\Pokemon Radical Red.sav' | Select-Object Name, Length, LastWriteTimeUtc
   ```

   The supported clean Radical Red 4.1 ROM MD5 is `8529f3a45d32bce4da637976fcf269d4`. A normal save is 131,072 bytes; mGBA may append 16 bytes of RTC state, producing 131,088 bytes. BizHawk's mGBA core accepts either form.

## Import into BizHawk

1. Use Auto-SoulLink's Manager or `/patcher` to apply the required companion patch to the matching clean Radical Red 4.1 ROM.
2. Open the companion-patched ROM once in BizHawk, let it reach the title screen, then close BizHawk.
3. Find BizHawk's configured **SaveRAM** directory. BizHawk has created a file named after the companion ROM with the `.SaveRAM` extension.
4. Back up that generated `.SaveRAM` file.
5. While BizHawk is closed, replace the generated file's contents with the mGBA `.sav`. Keep the filename BizHawk generated, including `.SaveRAM`.
6. Reopen the companion ROM in BizHawk and choose **Continue**.
7. Verify trainer name and ID, play time, badges, current location, party species, levels and PC contents before loading the SLink launcher.
8. In BizHawk, use **File → Save RAM → Flush Save Ram** after the first verified in-game save.

Keep the original mGBA `.sav`, the imported `.SaveRAM`, and the unmodified clean ROM until both players have completed at least one verified BizHawk save-and-reload cycle.
