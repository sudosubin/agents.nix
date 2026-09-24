{
  buildPythonPackage,
  fetchPypi,
  setuptools,
}:

buildPythonPackage rec {
  pname = "jsonyx";
  version = "2.3.0";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-d7FLMqrx8vd7mXEU2NyuvZGIW3VisHGGh+Tlcd2/t2I=";
  };

  build-system = [ setuptools ];
}
