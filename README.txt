OG CHECKER

OGChecker.exe is a Windows app that opens in its own window, with no
browser and no console. It uses Microsoft Edge WebView2, which is built
into Windows 10 and 11. Closing the window quits the app.

The website lives in the docs folder. GitHub builds the .exe for you,
and the website's Download button always gets the newest build.

=====================================================================
UPDATING AN EXISTING REPOSITORY (e.g. rsuf/UserTool)
=====================================================================
1. Unzip this zip.
2. On your repository page: Add file > Upload files.
3. Drag in everything inside the og-checker folder and click
   "Commit changes". Files with the same name are replaced.
   (The .github folder may be skipped by the browser. That's fine:
   the build.yml you already created stays as it is.)
4. The Actions tab starts a new build. After about 5 minutes, the new
   version appears under Releases, and the website's Download button
   switches to it automatically.

=====================================================================
FIRST-TIME SETUP
=====================================================================
1. Create a public repository on GitHub and upload everything inside
   this folder (Add file > Upload files).
2. If the .github folder didn't upload: Add file > Create new file,
   name it  .github/workflows/build.yml  and paste in the contents of
   that file from this folder. Commit.
3. Actions tab: wait for "Build Windows app" to show a green check.
4. Settings > Pages: Branch "main", folder "/docs", Save.
5. Your site appears at https://YOUR-NAME.github.io/REPO-NAME/

=====================================================================
GOOD TO KNOW
=====================================================================
- The first time someone opens the .exe, Windows shows "Windows
  protected your PC" because the app isn't signed with a paid
  certificate. They click "More info", then "Run anyway". The website
  FAQ explains this.
- On a very old Windows 10 without WebView2, the app opens in the
  browser instead, so it still works.
- Minecraft drop history is saved next to the .exe
  (minecraft_name_history.json).
- Put a bigger word list next to the .exe as words.txt (one word per
  line) to search more real words.
- Using your own domain: in docs/index.html, fill in
  const CONFIG = { owner: "your-github-name", repo: "your-repo", ... }

=====================================================================
EARNING FROM DOWNLOADS (AD LINK)
=====================================================================
1. Sign up at an ad-link service (Linkvertise and LootLabs are the
   common ones for downloads).
2. Create a new link. As the destination, use your direct download URL:
   https://github.com/YOUR-NAME/YOUR-REPO/releases/latest/download/OGChecker.exe
   (for you: https://github.com/rsuf/UserTool/releases/latest/download/OGChecker.exe)
   Because it says "latest", this one link keeps working for every
   new version.
3. On GitHub, open docs/index.html, click the pencil to edit, and find
   the CONFIG line near the bottom. Paste your ad link between the
   quotes after adLink, for example:
     adLink: "https://link-target.net/12345/og-checker",
4. Optional: set showSource: false to hide the footer link to your
   GitHub repo.
5. Commit. Every Download button now opens the ad page in a new tab.

Worth knowing:
- Anyone who finds your GitHub repo can still download from its
  Releases page directly, because public repos are public. Most
  people come through the website, so this rarely matters.
- Pick the service's least aggressive ad settings if you can.
  Pop-ups and fake "download" buttons on ad pages make people think
  the app itself is unsafe, on top of the Windows warning they
  already see.

Files
  app.py                     the Windows app (window + launcher)
  server.py, checker_core.py the checking engine
  static/index.html          the app's interface
  docs/                      the website
  OGChecker.spec, app.ico    the .exe recipe and icon
  .github/workflows/build.yml  tells GitHub how to build the .exe
  start.bat / start.sh       run from source with Python
  build_exe.bat              build the .exe yourself (needs Python)
