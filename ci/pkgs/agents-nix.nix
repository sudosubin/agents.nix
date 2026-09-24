{
  buildPythonPackage,
  jsonyx,
  urllib3,
  uv-build,
}:

buildPythonPackage {
  pname = "agents-nix";
  version = "0.1.0";
  pyproject = true;

  src = ../lib;

  build-system = [ uv-build ];

  dependencies = [
    jsonyx
    urllib3
  ];
}
