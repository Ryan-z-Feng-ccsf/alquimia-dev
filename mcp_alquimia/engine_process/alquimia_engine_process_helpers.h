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

#ifndef MCP_ALQUIMIA_ENGINE_PROCESS_HELPERS_H_
#define MCP_ALQUIMIA_ENGINE_PROCESS_HELPERS_H_

#include <stdbool.h>
#include <stdio.h>

#include "alquimia/alquimia_interface.h"
#include "cjson/cJSON.h"

/**
 * @brief Context and state manager for a single geochemical simulation session.
 *
 * This structure encapsulates the complete lifecycle of the Alquimia engine,
 * managing memory allocations, physical state vectors, error tracking, and execution
 * progression. It acts as the central state machine for the JSON-RPC dispatcher.
 */
typedef struct {
  
  /* --- Core Engine Components --- */
  AlquimiaInterface interface;
  AlquimiaData data;
  AlquimiaEngineStatus status;
  AlquimiaGeochemicalCondition condition;

  /* --- Lifecycle State Machine Flags --- */
  // Alquimia interface setup
  bool setup_complete;

  // Allocate Alquimia data (Alquimia State...)
  bool allocated; 

  // Process condition
  bool initialized;

  // Error
  bool failed;

  // ONNX inference has neither auxiliary pH nor iterative convergence.
  bool is_onnx;

  /* --- Simulation Physics & Tracking --- */
  double porosity;
  double time;
  // Reaction steps completed since initialization
  int completed_steps;
} SimulationEngineSession;

/* Creates an error reply; the caller owns the returned JSON tree. */
cJSON* Error(const char* operation, int error_code, const char* message);

/* Creates a success reply, taking ownership of result. */
cJSON* Success(cJSON* result);

/* Sends and frees reply, returning true if writing and flushing succeed. */
bool Send(FILE* response, cJSON* reply);

/* Returns a non-empty string owned by object, or NULL for an invalid field. */
const char* JsonToString(const cJSON* object, const char* key);

/* Dispatches a request and returns a reply owned by the caller. */
cJSON* Execute(SimulationEngineSession* simulation_session, const cJSON* request);

/* Shuts down the engine and frees session data; returns the shutdown error. */
int Cleanup(SimulationEngineSession* simulation_session);

#endif /* MCP_ALQUIMIA_ENGINE_PROCESS_HELPERS_H_ */
