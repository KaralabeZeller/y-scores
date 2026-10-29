# Upcoming schedule display

The native 192x64 display shows the next two unfinished fixtures in chronological
order. Large bold team names scroll only when they exceed their row, at 18 pixels
per second, pausing at the start and end. Short names remain still. Times and
categories remain fixed while names move. Changing fixtures restarts the scroll.

The header shows UP NEXT, a centered date in the configured display timezone,
and a right-aligned court label. Long court labels are bounded to the right-hand
header area; mixed courts show MULTI COURT. The first fixture is amber.
Category labels come from categoryName in the authorized schedule feed, with
CATEGORY TBD for older servers. Device settings and pairing are preserved.
