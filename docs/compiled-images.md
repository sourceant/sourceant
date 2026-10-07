# Compiled runtime images

The release image loads first-party Python modules as native extensions. Cython
and the C compiler run in a separate build stage. The release stages install dependencies from mounted wheels and copy only
compiled packages, license notices, and executable launchers.

Package resources are compressed into a native resource module. Runtime readers
load those resources directly. Database migrations retain their revision history
and run through the CLI. Plugins can contain native package initializers and
modules alongside ordinary Python plugins installed by users.

Build and inspect a release image:

```sh
docker build -t sourceant:compiled .
python3 scripts/audit_image.py sourceant:compiled
```

The audit examines every image layer, including files removed by later layers.
It rejects first-party source and plaintext package resources, including source
inside retained wheels.

The `test` target adds test dependencies to the compiled runtime. CI mounts the
tests separately, asserts that application imports resolve to native modules,
and runs the complete suite before publishing. Tests that inspect source text
read a separate checkout mount through `SOURCEANT_SOURCE_TEST_ROOT`.

Dependent images must use a compiled Core image as their base and retain the
same Python version and platform. Publish Core first, then update dependent
images to its immutable reference. Source-mounted development environments
continue to use ordinary Python modules.
