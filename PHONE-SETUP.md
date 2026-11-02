# Phone setup: how the phone tells the socket its battery level

Written 2026-10-09 from how these phone features normally work. Not yet tried against the real
socket, because the socket does not exist yet. Menu names can differ slightly between versions.

The phone reports by opening one web address:

    http://smartsocket.local/api/battery?level=NN

where NN is the battery percentage. The socket stops charging when NN is at or above the upper
limit and starts again when NN is at or below the lower limit. The phone must be on the same
Wi-Fi network as the socket.

Set this up only on the phone that is charged through the socket. If two phones both report,
the socket cannot tell them apart and will follow whichever spoke last.

## iPhone (Shortcuts app, nothing to install)

Make two automations, one for each limit. For the stop limit:

1. Open Shortcuts, tap Automation, tap + (New Automation).
2. Choose Battery Level. Slide to your stop level (for example 80 %) and choose "Rises Above".
3. Choose "Run Immediately" and turn "Notify When Run" off. Tap Next.
4. Tap "New Blank Automation", then "Add Action".
5. Add "Get Battery Level".
6. Add "Get Contents of URL". In the URL box type
   `http://smartsocket.local/api/battery?level=` and then, from the variables bar above the
   keyboard, insert "Battery Level" at the end.
7. Tap Done.

Repeat for the start limit: Battery Level, your start level (for example 40 %), "Falls Below",
the same two actions.

Sending the real level (steps 5 and 6) means the two automations stay correct even if you
change the limits on the socket's page later.

## Android (MacroDroid, free from the Play Store)

One macro is enough, because Android can report every change:

1. Open MacroDroid, tap Add Macro.
2. Trigger: Battery/Power, Battery Level, "Increase/Decrease", any change (or every 5 %).
3. Action: Web Interactions, HTTP Request. Method GET. URL:
   `http://smartsocket.local/api/battery?level=[battery]`
   (`[battery]` is MacroDroid's placeholder for the current percentage; pick it from the
   "magic text" button rather than typing it, in case the spelling differs in your version.)
4. Constraint (optional): Wi-Fi connected to your home network, so it stays quiet elsewhere.
5. Name the macro and save. Allow MacroDroid to run in the background when Android asks,
   otherwise the system stops it after a while.

## If the name does not work

Some phones and networks do not understand `smartsocket.local`. Open the socket's page from a
computer, look under Details for "Number address", and use that in place of the name, for
example `http://192.168.1.50/api/battery?level=...`. If the home router hands out a different
number later, the automation must be updated; most routers let you fix a device's number.

## Check that it works

1. Open the socket's page (or the Windows app) next to the phone.
2. Run the automation by hand once (iPhone: open it and tap the play button; MacroDroid: "Test
   actions").
3. The page should show the phone's percentage with "a few seconds ago" beside it.
