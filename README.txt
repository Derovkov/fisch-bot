FISCH BOT
=========

Casts, lures and reels in the Roblox game Fisch. It watches the screen and
clicks for you; it does not modify the game.

SETUP (once, and again after updating the bot)
  1. Double-click Install.bat. If Python isn't installed it offers to
     install Python 3.13 for you (winget); otherwise get it from
     https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  2. It installs what the bot needs and makes "Fisch bot" shortcuts with
     the bot's icon: in this folder, in the Start menu and (if you say yes)
     on your desktop.

EVERY TIME
  1. Open Roblox, join Fisch.
  2. Open the "Fisch bot" shortcut. Only the app window opens -- no
     terminal. (FischBot.pyw does the same; "Start Fisch bot.bat" still
     works but flashes a terminal for a moment.) Opening it again while
     it runs just brings the window to the front. If it can't start, a
     message says why; details are in tmp/startup.log.
  3. Rods page: equip your rod and set its enchants. Either open your
     Equipment Bag -> Fishing Rods in Roblox and press "Scan from game",
     or use the check (owned) and pencil (enchants) buttons on each rod card.
  4. Dashboard: press Start, or F7 with the app open. F9 stops it from any window.

GOOD TO KNOW
  * Settings > Hotkeys: change Start (F7), Stop/cancel searches (F9), and Next
    saved setup (F6). Save hotkeys applies them immediately. Type a key name
    or combination, such as ctrl+alt+r; use different keys for each action.
    These are stored in the general config, not individual rod setups.
    Stop cancels rod scans and quest reads too. An OCR read already in progress
    finishes before cleanup, but its cancelled quest results are discarded.
  * Help in the sidebar explains startup, shortcuts, rod checks and window sizes.
  * At start and every two minutes at the next cast boundary, the bot checks
    your named rod's held frame in the hotbar. It restores a readable unheld
    rod with its slot key, then verifies it. Unreadable labels are logged;
    the bot does not guess which item to take out.
  * Dashboard > Quick switch: Save setup keeps a named rod/enchant/control
    configuration. Use loads it while idle; Switch queues it after this cast.
    F6 cycles saved setups while Roblox stays in front. With "Equip rod in
    game" enabled, the bot switches rods through the Equipment Bag.
    The cast limit applies to the total casts in the current run.
  * Saved configurations are kept in fischbot_profiles.json. They contain
    settings only; run data and screenshots still follow the cleanup setting.
  * Reel activity shows the latest 24 reels. Expand its details for recent
    catch confirmations, or hover individual reels.
  * Useables > Totems shows the weather read between casts. The bot hovers
    new icons for their names and defers totems for active effects, protected
    weather groups or unreadable icons. Local events whose rules are not
    verified are deferred too. Limits and reserves still apply.
  * Misc > Lullaby buffs: pick which Lullaby buffs to grind and for how many
    minutes each. Between casts the bot opens the Equipment Bag, presses the
    matching mode button down the right side of the Lullaby's card, and
    closes the bag; after each buff's time it switches to the next and starts
    over after the last. One buff in the list: it stays on it. The Lullaby
    must be your rod. Like Useables, this is kept apart from saved setups.
  * Screen size: the first fish learns the reel scale ("UI scale ... locked").
    The bot follows window movement/resize between casts and relearns reel
    geometry after a resize. Finish the current fish before changing size,
    and keep the window steady during Equipment Bag actions. Small Equip
    labels get an additional OCR pass; the new fallback still needs live tests.
  * "Keep Roblox in front" means you can't use the PC while it runs: your
    clicks and keys would go to Roblox.
  * Every setting has a (?) - hover it for what it does.
  * Temporary files a run makes are deleted when it stops. Your settings
    are kept in fischbot_settings.json next to these files.
  * Roblox's terms of service may not allow macros or automation. Using this
    could put your account at risk - your call.

COMMUNITY & FEEDBACK
  Discord: https://discord.gg/avBvJjEWbm -- help, bug reports, ideas and
  rod skins the bot reads badly. In the app: Help > Community & feedback.
  For a bug, turn on Keep logs, reproduce it, and attach that run's bot.log.

Rod and enchant data: Fischipedia, the official Fisch wiki (fischipedia.org).
