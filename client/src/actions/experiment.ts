import { Action } from 'redux';
import * as url from 'url';

import { handleAuthError, urlifyProjectName } from '../constants/utils';
import { ExperimentModel } from '../models/experiment';
import { BASE_API_URL } from '../constants/api';
import { getOffset } from '../constants/paginate';
import * as paginationActions from '../actions/pagination';

export enum actionTypes {
  CREATE_EXPERIMENT = 'CREATE_EXPERIMENT',
  DELETE_EXPERIMENT = 'DELETE_EXPERIMENT',
  UPDATE_EXPERIMENT = 'UPDATE_EXPERIMENT',
  RECEIVE_EXPERIMENT = 'RECEIVE_EXPERIMENT',
  RECEIVE_EXPERIMENTS = 'RECEIVE_EXPERIMENTS',
  REQUEST_EXPERIMENTS = 'REQUEST_EXPERIMENTS',
}

export interface CreateUpdateReceiveExperimentAction extends Action {
  type: actionTypes.CREATE_EXPERIMENT | actionTypes.UPDATE_EXPERIMENT | actionTypes.RECEIVE_EXPERIMENT;
  experiment: ExperimentModel;
}

export interface DeleteExperimentAction extends Action {
  type: actionTypes.DELETE_EXPERIMENT;
  experiment: ExperimentModel;
}

export interface ReceiveExperimentsAction extends Action {
  type: actionTypes.RECEIVE_EXPERIMENTS;
  experiments: ExperimentModel[];
}

export interface RequestExperimentsAction extends Action {
  type: actionTypes.REQUEST_EXPERIMENTS;
}

export type ExperimentAction =
  CreateUpdateReceiveExperimentAction
  | DeleteExperimentAction
  | ReceiveExperimentsAction
  | RequestExperimentsAction;

export function createExperimentActionCreator(experiment: ExperimentModel): CreateUpdateReceiveExperimentAction {
  return {
    type: actionTypes.CREATE_EXPERIMENT,
    experiment
  };
}

export function updateExperimentActionCreator(experiment: ExperimentModel): CreateUpdateReceiveExperimentAction {
  return {
    type: actionTypes.UPDATE_EXPERIMENT,
    experiment
  };
}

export function deleteExperimentActionCreator(experiment: ExperimentModel): DeleteExperimentAction {
  return {
    type: actionTypes.DELETE_EXPERIMENT,
    experiment
  };
}

export function requestExperimentsActionCreator(): RequestExperimentsAction {
  return {
    type: actionTypes.REQUEST_EXPERIMENTS,
  };
}

export function receiveExperimentsActionCreator(experiments: ExperimentModel[]): ReceiveExperimentsAction {
  return {
    type: actionTypes.RECEIVE_EXPERIMENTS,
    experiments
  };
}

export function receiveExperimentActionCreator(experiment: ExperimentModel): CreateUpdateReceiveExperimentAction {
  return {
    type: actionTypes.RECEIVE_EXPERIMENT,
    experiment
  };
}

export function fetchExperiments(projectUniqueName: string, currentPage?: number, groupId?: string): any {
  return (dispatch: any, getState: any) => {
    dispatch(requestExperimentsActionCreator());
    paginationActions.paginateExperiment(dispatch, currentPage);
    let experimentsUrl = `${BASE_API_URL}/${urlifyProjectName(projectUniqueName)}`;
    if (groupId) {
      experimentsUrl += `/groups/${groupId}/experiments/`;
    } else {
      experimentsUrl += `/experiments/`;
    }
    let filters: {[key: string]: number|boolean|string} = {};
    if (!groupId) {
      filters.independent = true;
    }
    let offset = getOffset(currentPage);
    if (offset != null) {
      filters.offset = offset;
    }
    if (filters) {
      experimentsUrl += url.format({ query: filters });
    }
    return fetch(experimentsUrl, {
      headers: {
        'Authorization': 'token ' + getState().auth.token
      }
    })
      .then(response => handleAuthError(response, dispatch))
      .then(response => response.json())
      .then(json => json.results)
      .then(json => dispatch(receiveExperimentsActionCreator(json)));
  };
}

export function fetchExperiment(user: string, projectName: string, experimentId: number): any {
  return (dispatch: any, getState: any) => {
    dispatch(requestExperimentsActionCreator());
    return fetch(BASE_API_URL + `/${user}` + `/${projectName}` + `/experiments/` + `${experimentId}`, {
      headers: {
        'Authorization': 'token ' + getState().auth.token
      }
    })
      .then(response => handleAuthError(response, dispatch))
      .then(response => response.json())
      .then(json => dispatch(receiveExperimentActionCreator(json)));
  };
}
