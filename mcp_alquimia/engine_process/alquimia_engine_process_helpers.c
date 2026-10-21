/* -*-  mode: c; c-default-style: "google"; indent-tabs-mode: nil -*- */

/*
** Alquimia Copyright (c) 2013-2016, The Regents of the University of California,
** through Lawrence Berkeley National Laboratory (subject to receipt of any
** required approvals from the U.S. Dept. of Energy).  All rights reserved.
**
** Alquimia is available under a BSD license. See LICENSE.txt for more
** information.
**
** If you have questions about your rights to use or distribute this software,
** please contact Berkeley Lab's Technology Transfer and Intellectual Property
** Management at TTD@lbl.gov referring to Alquimia (LBNL Ref. 2013-119).
**
** NOTICE.  This software was developed under funding from the U.S. Department
** of Energy.  As such, the U.S. Government has been granted for itself and
** others acting on its behalf a paid-up, nonexclusive, irrevocable, worldwide
** license in the Software to reproduce, prepare derivative works, and perform
** publicly and display publicly.  Beginning five (5) years after the date
** permission to assert copyright is obtained from the U.S. Department of Energy,
** and subject to any subsequent five (5) year renewals, the U.S. Government is
** granted for itself and others acting on its behalf a paid-up, nonexclusive,
** irrevocable, worldwide license in the Software to reproduce, prepare derivative
** works, distribute copies to the public, perform publicly and display publicly,
** and to permit others to do so.
*/

/* JSON protocol helpers and simulation session operations. */

#include "alquimia_engine_process_helpers.h"

#include <limits.h>
#include <math.h>
#include <string.h>
#include <unistd.h>

#include "alquimia/alquimia_memory.h"
#include "alquimia/alquimia_constants.h"
#include "alquimia/alquimia_util.h"

// Upper bound on reaction steps per react request; mirrored by the Python
// TimeConfigRequest so an oversized loop is refused before it starts.
static const int kMaxReactSteps = 10000;

/**
 * @brief Constructs a structured JSON error response object for engine failures.
 * @param[in] operation Name of the subsystem or lifecycle phase where failure occurred.
 * @param[in] code Numeric error code indicating the underlying subsystem status.
 * @param[in] message Human-readable description explaining the failure reason.
 * @return Newly allocated cJSON root object representing the error payload, or NULL on allocation failure.
 *
 * The caller assumes ownership of the returned cJSON tree and is responsible
 * for freeing it (typically via Send()). Creates a top-level
 * {"success": false} wrapper enclosing an "error" dictionary with the operation context,
 * return code, and message.
 */
cJSON* Error(const char* operation, int error_code, const char* message)
{
  cJSON* reply = cJSON_CreateObject();
  cJSON_AddBoolToObject(reply, "success", false);
  cJSON* error = cJSON_AddObjectToObject(reply, "error");
  cJSON_AddStringToObject(error, "operation", operation);
  cJSON_AddNumberToObject(error, "code", error_code);
  cJSON_AddStringToObject(error, "message", message);
  return reply;
}

/**
 * @brief Constructs a structured JSON success response object.
 * @param[in] result cJSON object containing the output data of a successful operation.
 *                   Note: Ownership of this pointer is transferred to the returned root object.
 * @return Newly allocated cJSON root object representing the success payload.
 *
 * The caller assumes ownership of the returned cJSON tree and is responsible
 * for freeing it (typically via Send()). Creates a top-level {"success": true} wrapper 
 * enclosing the provided "result" payload. Because cJSON_AddItemToObject transfers 
 * memory ownership, freeing this returned root will automatically free the nested result object.
 */
cJSON* Success(cJSON* result)
{
  cJSON* reply = cJSON_CreateObject();
  cJSON_AddBoolToObject(reply, "success", true);
  cJSON_AddItemToObject(reply, "result", result);
  return reply;
}

/**
 * @brief Serializes a cJSON tree and transmits it over the response stream.
 * @param[in,out] response Open file stream targeted for IPC communication.
 * @param[in] reply Root cJSON object containing the structured payload to transmit.
 * @return True only if serialization succeeded, the payload was written, and the buffer was flushed.
 *
 * Formats the cJSON structure into a single-line unformatted string and appends
 * a trailing newline for line-delimited stream protocol consumers. Forces an immediate
 * fflush() to bypass standard stream buffering and prevent reader deadlocks.
 * Unconditionally consumes and frees both the serialized text buffer and the input reply tree.
 */
bool Send(FILE* response, cJSON* reply)
{
  char* text = cJSON_PrintUnformatted(reply);
  bool success = text && fprintf(response, "%s\n", text) >= 0 &&
            fflush(response) == 0;
  cJSON_free(text);
  cJSON_Delete(reply);
  return success;
}

/**
 * @brief Safely extracts a non-empty string field from a JSON object using strict case matching.
 * @param[in] object The parent cJSON object to inspect.
 * @param[in] key The case-sensitive property name to look up.
 * @return Pointer to the string value, or NULL if the key is missing, not a string, or is an empty string ("").
 *
 * This helper enforces strict type and boundary validation. By checking item->valuestring[0], 
 * it intentionally treats empty strings as invalid/missing data. The returned pointer is owned 
 * by the cJSON tree and must not be freed by the caller.
 */
const char* JsonToString(const cJSON* object, const char* key)
{
  const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
  return cJSON_IsString(item) && item->valuestring[0] ? item->valuestring : NULL;
}

/**
 * @brief Safely extracts and validates a finite floating-point number from a JSON object.
 * @param[in] object The parent cJSON object to inspect.
 * @param[in] key The case-sensitive property name to look up.
 * @param[out] value Pointer to the destination double where the extracted number will be stored.
 * @return True if the key exists, is a valid number, and is finite; otherwise false.
 *
 * This helper ensures numerical safety for the computational engine by rejecting missing 
 * keys, non-numeric types, and special floating-point values (NaN, Infinity). The output 
 * parameter `value` remains unmodified unless all validation checks pass.
 */
static bool JsonToNumber(const cJSON* object, const char* key, double* value)
{
  const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble))
    return false;
  *value = item->valuedouble;
  return true;
}

/**
 * @brief Extracts a strictly positive integer bounded by a maximum from a JSON object.
 * @param[in] object The parent cJSON object to inspect.
 * @param[in] key The case-sensitive property name to look up.
 * @param[in] maximum Largest accepted value.
 * @param[out] value Destination for the extracted integer.
 * @return True if the key holds a whole number in [1, maximum]; otherwise false.
 *
 * cJSON stores every number as a double, so fractional, non-finite and out-of-range
 * values are rejected before the cast. The output parameter `value` remains
 * unmodified unless all validation checks pass.
 */
static bool JsonToPositiveInt(const cJSON* object, const char* key, int maximum, int* value)
{
  const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
      item->valuedouble != floor(item->valuedouble) ||
      item->valuedouble < 1 || item->valuedouble > maximum)
    return false;
  *value = (int)item->valuedouble;
  return true;
}

/**
 * @brief Handles engine-level failures by marking the session failed and formatting an error response.
 * @param[in,out] simulation_session Active simulation session whose status holds the failure details.
 * @param[in] operation Subsystem or lifecycle phase where the engine failure occurred.
 * @return Newly allocated cJSON root object encoding the error details, or NULL on allocation failure.
 *
 * Latches simulation_session->failed to true to lock down the session and prevent subsequent
 * operations from running on corrupted engine state. Extracts the numeric error code and message
 * populated by the underlying chemistry interface status and delegates JSON construction to Error().
 */
static cJSON* EngineError(SimulationEngineSession* simulation_session, const char* operation)
{
  simulation_session->failed = true;
  return Error(operation, simulation_session->status.error, simulation_session->status.message);
}

/**
 * @brief Restores the fixed porosity and recomputes auxiliary output after a chemistry call.
 * @param[in,out] simulation_session Active session whose state and auxiliary output are updated.
 * @return True on success; false if the engine reported an error in simulation_session->status.
 *
 * Preserves the fixed-porosity convention of the supported batch benchmarks. Runs
 * after every reaction step, as BatchChemDriver does, so a looped react request
 * reproduces the same sequence of single-step requests.
 */
static bool RefreshAuxiliaryOutput(SimulationEngineSession* simulation_session)
{
  simulation_session->data.state.porosity = simulation_session->porosity;
  simulation_session->interface.GetAuxiliaryOutput(
    &simulation_session->data.engine_state,
    &simulation_session->data.properties,
    &simulation_session->data.state,
    &simulation_session->data.aux_data,
    &simulation_session->data.aux_output,
    &simulation_session->status
  );
  return !simulation_session->status.error;
}

/**
 * @brief Serializes an Alquimia double vector to a JSON array and appends it to a parent object.
 * @param[in,out] result Target cJSON object where the newly created array will be attached.
 * @param[in] key Property name (key) for the newly appended JSON array.
 * @param[in] vector Pointer to the Alquimia numerical vector to serialize.
 * @return True if successful; false if any element is non-finite (NaN or Infinity), leaving the parent object unmodified.
 *
 * Enforces strict JSON numerical compliance by pre-scanning the vector to ensure all 
 * values are finite, as NaN and Infinity are illegal in the JSON specification. 
 * Safely handles empty vectors (size == 0) by gracefully generating an empty JSON array ([]).
 */
static bool AlquimiaVectorDoubleToJson(cJSON* result, const char* key,
                   const AlquimiaVectorDouble* vector)
{
  for (int i = 0; i < vector->size; ++i)
  {
    if (!isfinite(vector->data[i]))
      return false;
  }
  cJSON_AddItemToObject(
    result, key,
    vector->size ? cJSON_CreateDoubleArray(vector->data, vector->size)
                 : cJSON_CreateArray()
  );
  return true;
}

/**
 * @brief Captures the current physical and chemical state of the simulation engine into a JSON object.
 * @param[in,out] simulation_session The active simulation session containing current state data.
 *                  (May be marked as failed if numerical corruption is detected).
 * @param[in] operation The name of the triggering operation (e.g., "react"), used for error reporting.
 * @return A cJSON object wrapped in Success() containing the snapshot, or an Error() payload on failure.
 *
 * Serializes core thermodynamic variables (temperature, porosity), geochemical properties (pH), 
 * and concentration vectors (mobile/immobile species, mineral fractions). Performs strict numerical 
 * validation across all exported data; if any NaN or Infinity values are encountered, it safely 
 * aborts serialization, permanently flags the session as failed, and returns an error.
 */
static cJSON* Snapshot(SimulationEngineSession* simulation_session, const char* operation) 
{
  AlquimiaState* state = &simulation_session->data.state;
  AlquimiaAuxiliaryOutputData* output = &simulation_session->data.aux_output;
  cJSON* result = cJSON_CreateObject();
  if ((!simulation_session->is_onnx && !isfinite(output->pH)) ||
      !isfinite(state->temperature) ||
      !AlquimiaVectorDoubleToJson(result, "total_mobile", &state->total_mobile) ||
      !AlquimiaVectorDoubleToJson(result, "total_immobile", &state->total_immobile) ||
      !AlquimiaVectorDoubleToJson(result, "mineral_volume_fraction", &state->mineral_volume_fraction))
  {
    cJSON_Delete(result);
    simulation_session->failed = true;
    return Error(operation, -1, "Engine returned non-finite output");
  }
  cJSON_AddNumberToObject(result, "time", simulation_session->time);
  cJSON_AddNumberToObject(result, "completed_steps", simulation_session->completed_steps);
  if (!simulation_session->is_onnx)
    cJSON_AddNumberToObject(result, "pH", output->pH);
  else
    cJSON_AddNullToObject(result, "pH");
  cJSON_AddNumberToObject(result, "temperature_celsius", state->temperature);
  cJSON_AddNumberToObject(result, "porosity", state->porosity);
  return Success(result);
}

/**
 * @brief Initializes the geochemical engine, validates physical parameters, and allocates session memory.
 * @param[in,out] simulation_session Target session to configure and allocate.
 * @param[in] request JSON object containing the setup parameters (engine, input_file, conditions, etc.).
 * @return Newly allocated cJSON root object with setup metadata on success, or an error payload on failure.
 *
 * Extracts and validates boundary constraints for physical properties (temperature, pressure, porosity, etc.).
 * Verifies read permissions for the chemistry input file before bootstrapping the underlying Alquimia 
 * interface. Upon successful engine initialization, allocates internal data structures and returns 
 * problem metadata including primary species names, mineral names, and reference units.
 */
static cJSON* Setup(SimulationEngineSession* simulation_session, const cJSON* request) 
{
  const char* chem_engine = JsonToString(request, "engine");
  const char* chem_input_file = JsonToString(request, "input_file");
  const char* cond_name = JsonToString(request, "initial_condition");
  AlquimiaState* state = &simulation_session->data.state;
  AlquimiaProperties* props = &simulation_session->data.properties;
  if (simulation_session->setup_complete) 
    return Error("setup", -1, "Already set up");

  if (!chem_engine || !chem_input_file || !cond_name || strlen(cond_name) > 255 ||
      !JsonToNumber(request, "density", &state->water_density) ||
      !JsonToNumber(request, "temperature", &state->temperature) ||
      !JsonToNumber(request, "pressure", &state->aqueous_pressure) ||
      !JsonToNumber(request, "porosity", &simulation_session->porosity) ||
      !JsonToNumber(request, "volume", &props->volume) ||
      !JsonToNumber(request, "saturation", &props->saturation))
  {
    return Error("setup", -1, "Missing or invalid setup parameters");
  }
  if (state->water_density <= 0 || state->temperature <= -273.15 ||
      state->aqueous_pressure <= 0 || simulation_session->porosity <= 0 ||
      simulation_session->porosity > 1 || props->volume <= 0 ||
      props->saturation <= 0 || props->saturation > 1)
  {
    return Error("setup", -1, "Physical parameters outside supported range");
  }

  // Reading access
  if (access(chem_input_file, R_OK) != 0)
    return Error("setup", -1, "Input file is not readable");
  
  // Set up the Alquimia interface
  CreateAlquimiaInterface(chem_engine, &simulation_session->interface, &simulation_session->status);
  if (simulation_session->status.error)
    return EngineError(simulation_session, "setup");
  simulation_session->is_onnx = AlquimiaCaseInsensitiveStringCompare(
      chem_engine, kAlquimiaStringOnnx);
  simulation_session->interface.Setup(chem_input_file, true, &simulation_session->data.engine_state,
                           &simulation_session->data.sizes, &simulation_session->data.functionality,
                           &simulation_session->status);
  if (simulation_session->status.error)
    return EngineError(simulation_session, "setup");
  simulation_session->setup_complete = true;
  if (simulation_session->data.sizes.num_primary <= 0) 
  {
    simulation_session->failed = true;
    return Error("setup", -1, "This engine process requires aqueous primary species");
  }

  // Allocate memory for Alquimia data
  AllocateAlquimiaData(&simulation_session->data);
  simulation_session->allocated = true;

  // Initial condition
  AllocateAlquimiaGeochemicalCondition((int)strlen(cond_name), 0, 0,
                                       &simulation_session->condition);
  strcpy(simulation_session->condition.name, cond_name);

  state->porosity = simulation_session->porosity;

  // Metadata
  simulation_session->interface.GetProblemMetaData(&simulation_session->data.engine_state,
                                        &simulation_session->data.meta_data,
                                        &simulation_session->status);
  if (simulation_session->status.error) 
    return EngineError(simulation_session, "setup");

  // JSON assemblage
  cJSON* result = cJSON_CreateObject();
  AlquimiaProblemMetaData* meta = &simulation_session->data.meta_data;
  cJSON_AddItemToObject(result, "primary_species", cJSON_CreateStringArray(
      (const char* const*)meta->primary_names.data, meta->primary_names.size));
  cJSON_AddItemToObject(result, "minerals", meta->mineral_names.size ?
      cJSON_CreateStringArray((const char* const*)meta->mineral_names.data,
                              meta->mineral_names.size) : cJSON_CreateArray());
  cJSON_AddBoolToObject(result, "has_pH", !simulation_session->is_onnx);
  cJSON* units = cJSON_AddObjectToObject(result, "units");
  cJSON_AddStringToObject(units, "total_mobile", "mol/L water");
  cJSON_AddStringToObject(units, "total_immobile", "mol/m^3 bulk");
  cJSON_AddStringToObject(units, "mineral_volume_fraction", "m^3 mineral/m^3 bulk");
  cJSON_AddStringToObject(units, "pH", "dimensionless");
  cJSON_AddStringToObject(units, "time", "s");
  cJSON_AddStringToObject(units, "temperature_celsius", "degC");
  cJSON_AddStringToObject(units, "porosity", "dimensionless");
  return Success(result);
}

/**
 * @brief Main event dispatcher and state machine for the simulation engine.
 * @param[in,out] simulation_session The active simulation session managing engine state.
 * @param[in] request The parsed JSON request containing the target "operation".
 * @return Newly allocated cJSON root object representing the operation result or error.
 *
 * Routes incoming operations ("setup", "initialize", "react", "get_results") while enforcing
 * strict lifecycle sequencing (setup -> initialize -> react). Implements a fall-through 
 * optimization: successful "initialize" and "react" commands automatically compute auxiliary 
 * output and yield a state snapshot, minimizing IPC overhead by avoiding separate polling requests.
 */
cJSON* Execute(SimulationEngineSession* simulation_session, const cJSON* request) {
  // Extract operation
  const char* operation = JsonToString(request, "operation");
  if (!operation) 
    return Error("request", -1, "Missing operation");
  if (simulation_session->failed) 
    return Error(operation, -1, "Simulation engine session failed; close and start a new engine process");

  // Setup the Alquimia interface
  if (strcmp(operation, "setup") == 0) 
    return Setup(simulation_session, request);
  if (!simulation_session->setup_complete) 
    return Error(operation, -1, "Call setup first");
  
  // Initialize the condition
  if (strcmp(operation, "initialize") == 0) 
  {
    if (simulation_session->initialized)
      return Error(operation, -1, "Already initialized");
    
    simulation_session->interface.ProcessCondition(&simulation_session->data.engine_state,
        &simulation_session->condition, &simulation_session->data.properties, &simulation_session->data.state,
        &simulation_session->data.aux_data, &simulation_session->status);
    if (simulation_session->status.error)
      return EngineError(simulation_session, operation);
    simulation_session->initialized = true;
  }
  else if (strcmp(operation, "react") == 0) 
  {
    double dt;
    int max_steps;
    if (!simulation_session->initialized)
      return Error(operation, -1, "Call initialize first");
    
    // Reject every invalid value before the loop, leaving the session usable
    if (!JsonToNumber(request, "dt", &dt) || dt <= 0)
      return Error(operation, -1, "dt must be a finite, positive number of seconds");
    if (!JsonToPositiveInt(request, "max_steps", kMaxReactSteps, &max_steps))
    {
      char message[128];
      snprintf(message, sizeof(message), "max_steps must be an integer from 1 to %d",
               kMaxReactSteps);
      return Error(operation, -1, message);
    }
    // A dt too small to change time would loop without advancing it
    if (simulation_session->time + dt == simulation_session->time ||
        !isfinite(simulation_session->time + dt * max_steps))
      return Error(operation, -1, "dt is too small or too large to advance the simulation time");
    if (max_steps > INT_MAX - simulation_session->completed_steps)
      return Error(operation, -1, "Session step count would overflow");
    
    // Reaction loop
    for (int step = 1; step <= max_steps; ++step)
    {
      simulation_session->status.converged = false;
      simulation_session->interface.ReactionStepOperatorSplit(&simulation_session->data.engine_state,
          dt, &simulation_session->data.properties, &simulation_session->data.state,
          &simulation_session->data.aux_data, -999, &simulation_session->status);
      if (simulation_session->status.error)
        return EngineError(simulation_session, operation);
      // ONNX reports inference failures through status.error and has no solver
      // convergence flag. Chemistry solvers must still explicitly converge.
      if (!simulation_session->is_onnx && !simulation_session->status.converged)
      {
        char message[160];
        simulation_session->failed = true;
        snprintf(message, sizeof(message),
                 "Reaction step %d of %d did not converge; simulation_session cannot be continued",
                 step, max_steps);
        return Error(operation, -1, message);
      }
      simulation_session->time += dt;
      ++simulation_session->completed_steps;
      // The final step's refresh is shared with initialize below
      if (step < max_steps && !RefreshAuxiliaryOutput(simulation_session))
        return EngineError(simulation_session, operation);
    }
  }
  else if (strcmp(operation, "get_results") == 0) 
  {
    if (!simulation_session->initialized)
      return Error(operation, -1, "Call initialize first");
    return Snapshot(simulation_session, operation);
  }
  else 
  {
    return Error(operation, -1, "Unknown operation");
  }

  // Fetch auxiliary data
  if (!RefreshAuxiliaryOutput(simulation_session))
    return EngineError(simulation_session, operation);
  return Snapshot(simulation_session, operation);
}

/**
 * @brief Safely tears down the simulation session and releases allocated memory.
 * @param[in,out] simulation_session The active session to clean up and destroy.
 * @return Error code from the underlying engine's shutdown process, or 0 on success.
 *
 * Conditionally invokes the native engine shutdown only if setup was fully completed, 
 * preventing segmentation faults from corrupted or partially initialized states. 
 * Frees all internal Alquimia data buffers and condition structures if they were allocated.
 */
int Cleanup(SimulationEngineSession* simulation_session) 
{
  int error = 0;
  if (simulation_session->setup_complete) 
  {
    simulation_session->status.error = 0;
    simulation_session->interface.Shutdown(&simulation_session->data.engine_state, &simulation_session->status);
    error = simulation_session->status.error;
  }
  /* A failed native Setup may leave an unusable pointer; process exit reclaims
   * such partial engine allocations instead of calling Shutdown on them. */
  if (simulation_session->allocated) 
  {
    FreeAlquimiaGeochemicalCondition(&simulation_session->condition);
    FreeAlquimiaData(&simulation_session->data);
  }
  return error;
}
