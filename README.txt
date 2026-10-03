FISCH BOT
=========

Casts, lures and reels in the Roblox game Fisch. It watches the screen and
clicks for you; it does not modify the game.

SETUP (once)
  1. Install Python 3.13 from https://www.python.org/downloads/
     During setup, tick "Add python.exe to PATH".
  2. Double-click Install.bat and wait for "Done".

EVERY TIME
  1. Open Roblox, join Fisch, and MAXIMISE the Roblox window.
  2. Double-click "Start Fisch bot.bat".
  3. Rods page: equip your rod and set its enchants. Either open your
     Equipment Bag -> Fishing Rods in Roblox and press "Scan from game",
     or use the check (owned) and pencil (enchants) buttons on each rod card.
  4. Dashboard: press Start. F9 stops it from any window.

GOOD TO KNOW
  * Dashboard > Quick switch: Save setup keeps a named rod/enchant/control
    configuration. Use loads it while idle; Switch queues it after this cast.
    F6 cycles saved setups for the current rod while Roblox stays in front.
    Configurations do not equip rods in the game. Equip the matching rod yourself.
    The cast limit applies to the total casts in the current run.
  * Saved configurations are kept in fischbot_profiles.json. They contain
    settings only; run data and screenshots still follow the cleanup setting.
  * Reel activity shows the latest 24 reels. Expand its details for recent
    catch confirmations, or hover individual reels.
  * Screen size: it was measured on a 1920-pixel-wide screen with Roblox
    maximised. Other window sizes are worked out on the first fish of each
    run (the log says "UI scale ... locked"). This is new and not yet
    tested in the game at other sizes, so maximised 1920 wide is still the
    safest setup.
  * "Keep Roblox in front" means you can't use the PC while it runs: your
    clicks and keys would go to Roblox.
  * Every setting has a (?) - hover it for what it does.
  * Temporary files a run makes are deleted when it stops. Your settings
    are kept in fischbot_settings.json next to these files.
  * Roblox's terms of service may not allow macros or automation. Using this
    could put your account at risk - your call.

Rod and enchant data: Fischipedia, the official Fisch wiki (fischipedia.org).
