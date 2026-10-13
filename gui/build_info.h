#pragma once

// What this binary was built from, as one line of JSON (schema op1.build.v1):
// host and bcore revisions with a dirty flag for tracked changes, build type,
// profiling, compiler. Printed by --build-info and recorded in profiling
// traces, so a comparison can tell which two builds it measured.
const char* BuildInfoJson();
