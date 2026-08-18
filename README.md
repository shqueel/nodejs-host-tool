# Node JS Hosting Tool

***Please bare in mind this is a clanker written tool that cost a couple of tokens***

<img width="849" height="519" alt="image" src="https://github.com/user-attachments/assets/c871f372-45c5-4bb6-bc3e-86afa43511b9" />


This app requires you to do the setup, this literally just runs the following commands, but into a UI wrapper:

```
npm start
npm run dev
npm run dev -- --host
npm run build
```

`npm run dev -- --host` is the "NPM Run Host Dev" button, for when you want the dev server reachable from other machines on your network rather than just localhost.

This also allow you to seperate your front and back end files, if you seperate them. (As seen in screenshot above).

Each directory you add runs independently: you can have `npm run dev` going on one project and `npm start` on another at the same time. The dropdown shows a ● next to any project with something running, and the buttons/statuses always reflect the project currently selected. Within a single project only one of start / dev / host dev can run at once, since they share the same directory and port.

## To run:

1. Unzip File,
2. Open PowerShell, go to directory and run ./install.ps1 (This will install Python and NodeJS),
3. Copy `NPM Host.ink` to anywhere or leave in the file,
4. Open `NPM Host.ink` and select `Add Directory` which will prompt you to enter a display name, frontend directory and backend directory.\***If you only have front/back end, you can put both directories in the same box***
5. Et Voila


### Any issues please log within issues tab, and I will review. Thanks
