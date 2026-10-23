# Test-server walkthrough

Run this on the test server with the test bot before the tournament. You need your own account
(admin) and at least three alt accounts (A, B, C). Tick each line as it works.

## Setup
- [ ] `/setup panel` in the sign-up channel posts the panel with three buttons.
- [ ] After `/signups close`, Create team and Apply answer "Sign-ups are closed".
      `/signups open` brings them back.

## Creating a team
- [ ] A clicks **Create team**, types a team name and a misspelled character name, and gets a
      list of matches with guilds to pick from.
- [ ] The category `Team N - NAME` appears with #manage-team, #team-applications, #mass and
      #team-chat. A gets a ping and the guide in #manage-team.
- [ ] B can't see any of those channels yet.
- [ ] The team is **not** on the public panel (fewer than 3 members).

## Joining
- [ ] B clicks **Apply to team**, picks the team and their character. A is pinged in
      #team-applications. B applies to a second team: the first application disappears.
- [ ] A clicks **Accept**. B gets a DM and the team role, and sees #mass and #team-chat.
- [ ] A uses **Invite member** on C. C gets a DM, clicks Accept, picks their character, and joins.
- [ ] With 3 members the team now shows on the public panel, by name only.

## Managing
- [ ] **Helpers** on B: B can now see #team-applications and post in #mass.
- [ ] In #mass, C can't post but can reply in a thread. A and B can mention the team role.
- [ ] **Role colour** `#E67E22` changes the role colour.
- [ ] **Remove member** on C: C gets a DM and loses the role.
- [ ] C clicks **Leave team** while not in a team: "You aren't in a team".
- [ ] **Transfer leadership** to B: B sees #manage-team, A doesn't.

## Disband and restore
- [ ] B (leader) clicks **Disband team**: roles disappear, channels are read-only for B.
- [ ] B clicks **Create team**, picks **Restore my old team**: roles come back for A and B.
- [ ] Disband again, then **Create a new team**: the old category is deleted.

## Admin
- [ ] `/rosters lock`: Accept, Invite and Leave are refused for players. `/rosters unlock`.
- [ ] `/team rename`, `/team set-leader`, `/team add`, `/team remove`, `/team extend` and
      `/team info` work on a team picked from the autocomplete list.
- [ ] `/admin-character set` links a character for someone else.
- [ ] `/export` returns a CSV with everyone's character name and ID.
- [ ] Every action above shows up in the log channel.

## Deadline (optional, needs a few days)
- [ ] A team left at 1 member gets a warning after 48 hours and is deleted after 72 hours.

## Shared leader role

- [ ] Create a "Team Leader" role and a channel only that role can see, then run `/setup leader-role` with it. Every current leader gets the role.
- [ ] Create a team: the new leader gets the role and can see the leaders channel, but not other teams' #manage-team.
- [ ] Transfer leadership: the role moves to the new leader.
- [ ] Disband a team, then restore it: the role goes and comes back.
- [ ] Run `/setup leader-role` with no role: nobody gets it any more.
