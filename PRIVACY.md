# Privacy

This plugin downloads the NDJSON dump (and, when given a manifest, its listed
export files) from the URL you provide and turns each record into a Knowledge
base document. It sends no data to third parties; requests go only to the source
you configure. An optional Bearer token / Authorization header is used solely to
authenticate against a private dump you point it at.
