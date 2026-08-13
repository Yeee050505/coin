import axios from 'axios';

const api = axios.create({ baseURL: '/api', timeout: 30000 });

export const getProjects = () => api.get('/projects').then(r => r.data);
export const getProjectDetail = (id: number) => api.get(`/projects/${id}`).then(r => r.data);
export const createProject = (data: any) => api.post('/projects', data).then(r => r.data);
export const followupProject = (id: number, question: string) => api.post(`/projects/${id}/followup`, { question }).then(r => r.data);
export const deleteProject = (id: number) => api.delete(`/projects/${id}`).then(r => r.data);
export const deleteProjects = (ids: number[]) => api.post('/projects/batch-delete', ids).then(r => r.data);
export const getDashboard = () => api.get('/dashboard/summary').then(r => r.data);

export const getReport = (id: number) => api.get('/reports/' + id).then(r => r.data);

export const downloadReport = (id: number) => api.get('/reports/' + id + '/download', { responseType: 'blob' }).then(r => r.data);
