# probe-cicd

Scratch reusable workflows and scripts for platform probes, called from scratch repositories. Nothing here is a
product, and nothing here runs on its own, since every workflow is `workflow_call` alone.

- `.github/workflows/deploy-python.yml`, a Python deploy to Vercel, created without its domains, smoked, then promoted.
- `.github/workflows/deploy-nextjs.yml`, a Next.js deploy built here, deployed prebuilt without its domains, smoked
  through the protection bypass, then promoted.
- `.github/workflows/identity-reusable.yml`, a run's identity as a called job sees it.
- `scripts/vercel_probe.py` and `scripts/gh_probe.py`, the standard library probes those workflows run.

Every probe prints comparisons, ids, hostnames and times alone, never a token, a bypass value or a setting's value.
