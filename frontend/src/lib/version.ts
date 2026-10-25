/**
 * Application version, shown next to the brand mark.
 *
 * ``package.json`` is the single place to bump: the import is resolved at build
 * time and only the version string survives into the bundle.
 */
import pkg from "../../package.json";

export const APP_VERSION: string = pkg.version;
