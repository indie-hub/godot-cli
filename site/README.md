# Website source

Buildless HTML/CSS/JavaScript with existing trial media. The timelapse loads only after Play is pressed. Google Fonts are optional with system fallbacks; there is no backend or analytics.

Preview from the repository root:

```sh
python3 -m http.server 8088 --bind 127.0.0.1 --directory site
```

Open http://127.0.0.1:8088 and stop the server with Ctrl+C when finished.

## Docs versus deployment artifacts

`docs/` contains command documentation and plans. `site/` contains the public website. A deployment artifact is a bundle of files selected for hosting: in this case, the website HTML, styles, script and media. It does not publish the whole repository.

GitHub Pages branch publishing accepts the root or `/docs`, not an arbitrary `/site` folder. To retain this layout, a future GitHub Actions workflow should upload `site/` with upload-pages-artifact, then deploy it with deploy-pages. No Node build is needed. That keeps planning documents out of the rendered website; they remain visible in a public repository.

The deployment workflow is `.github/workflows/pages.yml`. It uploads only `site/`, runs when site files or the workflow change on `main`, and also supports manual runs. Repository settings have not been changed. Before publishing, review the demo, tested-support statement, installation commands and default-branch links. Trial imagery illustrates development; it does not certify every gameplay requirement or imply a flawless autonomous build.

## Publish for the first time

1. Open [the repository's Pages settings](https://github.com/indie-hub/godot-cli/settings/pages). Under **Build and deployment → Source**, select **GitHub Actions**. You need repository administration permission. Do not select branch publishing from `/docs`.
2. From the repository root, review and commit only the website and workflow:

   ```sh
   git add site/ .github/workflows/pages.yml
   git diff --cached --stat
   git diff --cached
   git commit -m "docs: add project website and Pages publishing"
   git push origin main
   ```

   These commands assume the current branch is `main`, as checked during setup. If other changes are already staged, they will also enter the commit: review the staged diff first. The other modified/untracked project files are intentionally not added by this command. If the repository requires pull requests, push a branch and merge it into `main` instead; deployment starts after the merge. Do not force-push if the remote rejects your push.
3. Open [GitHub Actions](https://github.com/indie-hub/godot-cli/actions) and select **Publish website**. Wait for the deployment to succeed; its environment link shows the actual site URL.
4. The expected default address is [indie-hub.github.io/godot-cli](https://indie-hub.github.io/godot-cli/). This is an expected address, not confirmation that the site is live. A configured custom domain can change it.

If the workflow ran before Pages was enabled, select **Publish website → Run workflow → main** after completing step 1. If Actions or the `github-pages` environment requires approval, follow the repository's existing policy. Subsequent pushes touching `site/` deploy automatically.

The site is public once deployed. The local workflow was prepared from [GitHub's official custom-workflow guidance](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages); no GitHub settings, commits, pushes or deployments were performed during preparation.
