{
  buildNpmPackage,
  fetchFromGitHub,
  nodejs,
  lib,
}:

let
  node-gyp-11_4_0 = buildNpmPackage rec {
    pname = "node-gyp";
    version = "11.4.0";

    src = fetchFromGitHub {
      owner = "nodejs";
      repo = "node-gyp";
      rev = "refs/tags/v${version}";
      hash = "sha256-VtomUV+0kTp34IuS0D0dR4ZMWpk4Ptpk1CBP8rdW2a4=";
    };

    postPatch = ''
      ln -s ${./node-gyp-11-4-0-package-lock.json} package-lock.json
      # Mocha 11 has no release with patched diff/serialize-javascript ranges.
      ${nodejs}/bin/node -e 'const fs = require("fs"); const manifest = JSON.parse(fs.readFileSync("package.json")); manifest.overrides = JSON.parse(fs.readFileSync("${./node-gyp-11-4-0-overrides.json}")); fs.writeFileSync("package.json", JSON.stringify(manifest));'
    '';

    npmDepsHash = "sha256-xsnYz08machiNQ7U37hK5T6G3BSjRCBmxjNA9vGCSnI=";

    npmDepsFetcherVersion = 2;

    dontNpmBuild = true;

    makeWrapperArgs = [ "--set npm_config_nodedir ${nodejs}" ];

    meta = {
      description = "Node.js native addon build tool";
      homepage = "https://github.com/nodejs/node-gyp";
      license = lib.licenses.mit;
      mainProgram = "node-gyp";
    };
  };
in
node-gyp-11_4_0
