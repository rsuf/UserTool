og//checker (OG Checker)

What you get after setup (free, about 10 minutes, no coding):
  - OGChecker.exe: one file people download and double-click.
    No Python or installing needed.
  - A landing page at https://YOUR-NAME.github.io/og-checker/
    with a Download button that always gets the newest version.

GitHub builds the .exe for you on its own Windows computers, so you
don't need Python either.

=====================================================================
STEP 1 - Put the files on GitHub
=====================================================================
1. Sign up at github.com (free).
2. Click "+" (top right) > New repository.
   Name: og-checker   ·   Public   ·   Create repository
3. On the next page, click "uploading an existing file".
4. Open the unzipped og-checker folder, select EVERYTHING inside
   it (including the .github, docs and static folders) and drag it onto
   the page. Click "Commit changes".

   Check: the file list on GitHub should show a ".github" folder.
   If it's missing, see "If the .github folder didn't upload" below.

=====================================================================
STEP 2 - Let GitHub build the .exe
=====================================================================
1. Click the "Actions" tab of your repository.
   - If it asks, click "I understand my workflows, go ahead and enable them".
2. You'll see "Build Windows app" running (yellow dot). It takes about
   3 minutes. When it turns green, the .exe is done.
   - Nothing running? Click "Build Windows app" on the left, then
     "Run workflow" > "Run workflow".
3. The .exe now appears under "Releases" on the right side of your
   repository's main page.

=====================================================================
STEP 3 - Turn on the landing page
=====================================================================
1. In your repository, click Settings > Pages (left menu).
2. Under "Branch", pick "main", then pick "/docs", then click Save.
3. Wait 1-2 minutes and refresh. GitHub shows your link at the top:
   https://YOUR-NAME.github.io/og-checker/
That's the link you share. The Download button finds your newest
.exe by itself.

=====================================================================
Updating the app later
=====================================================================
Upload changed files to GitHub (Add file > Upload files). GitHub
builds a new .exe automatically, and the landing page's button
switches to it. Changing only the landing page (docs folder) doesn't
trigger a new build.

=====================================================================
If the .github folder didn't upload
=====================================================================
Some browsers skip folders that start with a dot. Create it by hand:
1. On your repository page: Add file > Create new file.
2. In the name box, type exactly:  .github/workflows/build.yml
3. Open .github/workflows/build.yml from the zip in Notepad, copy
   everything, and paste it into the big box on GitHub.
4. Click "Commit changes". The build starts by itself.

=====================================================================
Good to know
=====================================================================
- Windows will show "Windows protected your PC" the first time people
  open the .exe, because it isn't signed with a paid certificate.
  They click "More info" > "Run anyway". The FAQ on the landing page
  explains this.
- The app opens in the browser and runs on the user's own PC. It
  closes with the "Quit app" button, or by itself a few minutes after
  the tab is closed.
- Minecraft drop history is saved next to the .exe
  (minecraft_name_history.json).
- Put a bigger word list next to the .exe as words.txt (one word per
  line) to search more real words.

Files
  server.py, checker_core.py, static/   the app
  OGChecker.spec, app.ico         the .exe recipe and icon
  .github/workflows/build.yml           tells GitHub how to build the .exe
  docs/                                 the landing page
  start.bat / start.sh                  run from source with Python
  build_exe.bat                         build the .exe yourself (needs Python)
