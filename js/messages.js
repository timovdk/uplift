// What the cat mascot says in the top bar. Add your own lines; one is picked at random
// whenever the situation changes (not on every redraw).
// Lines must stay at most 60 characters, so they fit on two lines and the bar keeps the
// same height (checked by `npm test`).

// Mijn stations: nothing starred yet.
export const MINE_EMPTY = [
  'Geef een station een ster, dan let ik op de liften.',
  'Welke stations gebruik jij? Tik op de ster, ik onthoud ze.',
];

// Mijn stations: every lift at your stations works.
export const MINE_OK = [
  'Alle liften bij jouw stations doen het. Goede reis!',
  'Liften werken. Vandaag even niks om je druk over te maken.',
  'Groen licht bij al je stations. Miauw!',
  'Alles werkt. Zo hoort het eigenlijk altijd te zijn.',
];

// Mijn stations: at least one lift is out of service.
export const MINE_DOWN = [
  'Balen: er is een lift stuk bij een van je stations.',
  'Een lift doet het niet. Plan wat extra tijd in.',
  'Lift kapot. Niet jouw schuld, wel even omplannen.',
  'Er hapert een lift. Ik heb hem hieronder voor je klaar.',
];

// Mijn stations: nothing broken, but some lifts have no (recent) status.
export const MINE_UNKNOWN = [
  'Van een paar liften weet ik niks. Twijfel? Check het even.',
  'Niet alles is bekend. Afhankelijk van de lift? Boek hulp.',
];

// Mijn stations, while the status is still loading.
export const LOADING = [
  'Even kijken hoe het met de liften gaat…',
];

// Zoeken tab.
export const FIND = [
  'Waar ga je heen? Ik kijk of de liften werken.',
  'Zoek een station, of laat me in de buurt snuffelen.',
  'Nieuw station? Even kijken hoe het met de liften zit.',
];

// Instellingen tab.
export const SETTINGS = [
  'Je stations blijven op je telefoon. Niemand kijkt mee.',
  'Hier staat waar mijn informatie vandaan komt.',
];

// The status is old: ProRail went quiet, or our update stopped.
export const STALE = [
  'Ik heb al even niks van ProRail gehoord. Klopt het nog?',
];

// No connection.
export const OFFLINE = [
  'Geen internet. Dit is wat ik het laatst wist.',
];

export const pick = (list) => list[Math.floor(Math.random() * list.length)];
