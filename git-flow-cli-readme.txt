SOC multi-user git flow
=======================

Branches
  main              protected trunk — almost NEVER push directly
  staging           shared integration (origin/staging)
  staging-<you>     YOUR daily branch (staging-dalton, staging-ian, ...)

Daily (each person)
  .\git-flow.ps1 -UserName dalton
  → 1  Save & push MY staging

Get teammates' merged work
  → 3  Pull origin/staging into MY staging

Publish your work to the team
  → 2  Compare
  → 4  Merge MY staging → shared staging + push

Promote to main (rare)
  → 6  GATED: checklist + type "PROMOTE MAIN"
  Prefer a Pull Request if the host protects main (see below).

------------------------------------------------------------------------------
REMOTE PROTECTION (do this on GitHub/GitLab/Bitbucket — the real backstop)
------------------------------------------------------------------------------
Script gates are helpful; server-side rules are mandatory so nobody can
`git push origin main` by accident.

GitHub (Settings → Branches → Branch protection rule on "main"):
  [x] Require a pull request before merging
  [x] Require approvals (at least 1 if two+ people)
  [x] Do not allow bypassing the above settings
  [x] Restrict who can push to matching branches (empty = no direct pushes)
  Optional: require status checks / conversation resolution

  Same idea optional on "staging" if you want PRs into staging too;
  often staging allows push from the team, main does not.

GitLab: Protected branches → main → Allowed to merge: Maintainers;
  Allowed to push: No one (merge via MR only).

Policy for this team
  - Default write target: staging-<you>
  - Integrate via origin/staging
  - main only via menu 6 (checklist) OR a PR from staging → main
  - Never Prefer-Remote / hard-reset on main or staging unless recovering
    a known-bad local clone

First-time setup
  git fetch origin
  git checkout main && git pull origin main
  git checkout -b staging main && git push -u origin staging   # if missing
  .\git-flow.ps1 -UserName <you>   # option 1 creates staging-<you>
