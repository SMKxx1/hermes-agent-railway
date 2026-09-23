{
  buildNpmPackage,
  fetchFromGitHub,
  jq,
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

    # Only the build tool is packaged. Its upstream test/lint dependencies are
    # unused (dontNpmBuild) and must not be fetched into the Nix closure.
    postPatch = ''
      ${jq}/bin/jq 'del(.devDependencies)' package.json > package.json.tmp
      mv package.json.tmp package.json
      ln -s ${./node-gyp-11-4-0-package-lock.json} package-lock.json
    '';

    npmDepsHash = "sha256-F7QTUSpxoHQ9cDnIpDjHkHIXETRmOMNJwQC60Q5jofI=";

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
