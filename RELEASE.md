# How to create a release

This guide outlines the steps for creating a release for this project, including updating the changelog and creating a release branch.

## 1. Create a release branch

If you want to isolate release-related changes from ongoing development, create a release branch.

Example:
```
git checkout -b release-1.0.0
```

## 2. Update the changelog

Before creating a release, update the changelog to document the changes introduced in this version. Follow the format specified in the existing changelog file (`CHANGELOG.md`).

Example:
```markdown
## [1.0.0] - 2025-02-28
- feat: Add support for Gemini LLM
- feat: Posting to reviews to github
```

## 3. Update version number

Update version number in [src/api/main.py](src/api/main.py)

## 4. Commit changes locally

Commit the changes to the changelog and version number locally using Git.

Example:
```
git add CHANGELOG.md
git commit -m "Update changelog for version 1.0.0"
```

## 5. Tag the release

Create a Git tag for the release.

Example:
```
git tag -a v1.0.0 -m "Release 1.0.0"
```

## 6. Push changes to remote repository

Push the changes and tags to the remote repository.

Example:
```
git push origin release-1.0.0
git push origin v1.0.0
```

## 7. Create the release on GitHub

1. Navigate to the "Releases" section of your GitHub repository.
2. Click on the "Draft a new release" button.
3. Fill in the release information, including the tag version, release title, and release notes.
4. Attach any relevant release assets.
5. Click "Publish release" to finalize the release.

## 8. Verify the release

Ensure that the release is visible in the "Releases" section of your GitHub repository and that the changelog accurately reflects the changes in this version.

