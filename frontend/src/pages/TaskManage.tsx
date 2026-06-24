import React, { useState, useEffect } from 'react';
import {
  Card, Button, Form, Input, Select, Space, Table, Tag, Modal, message, Typography, Row, Col,
  Descriptions, Spin, Divider, Alert, Progress,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, RobotOutlined, BarChartOutlined, FileTextOutlined,
  ThunderboltOutlined, DeleteOutlined, ExclamationCircleOutlined, DownloadOutlined,
  CheckCircleOutlined, CloseCircleOutlined, SyncOutlined, ClockCircleOutlined,
} from '@ant-design/icons';
import { getProjects, getProjectDetail, createProject, deleteProject, deleteProjects, downloadReport } from '../services/api';
import { getReport } from '../services/api';

const { Text, Title } = Typography;
const { TextArea } = Input;

const statusConfig: Record<string, { color: string; icon: React.ReactNode; text: string }> = {
  pending: { color: '#999', icon: <ClockCircleOutlined />, text: '等待中' },
  running: { color: '#1A7DFF', icon: <SyncOutlined spin />, text: '运行中' },
  success: { color: '#00C853', icon: <CheckCircleOutlined />, text: '已完成' },
  failed: { color: '#FF4D4F', icon: <CloseCircleOutlined />, text: '失败' },
};

const TaskManage: React.FC = () => {
  const [projects, setProjects] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [form] = Form.useForm();
  const [report, setReport] = useState<any>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [reportLoading, setReportLoading] = useState(false);

  const fetch = async () => {
    setLoading(true);
    try { setProjects(await getProjects()); } catch { message.error('获取失败'); }
    setLoading(false);
  };
  useEffect(() => { fetch(); }, []);

  const handleCreate = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    try {
      const r = await createProject(values);
      message.success(`创建成功 ID: ${r.project_id}`);
      setModalOpen(false);
      form.resetFields();
      fetch();
    } catch (e: any) { message.error('创建失败'); }
  };
  const handleDownload = (p: any) => {
    downloadReport(p.id).then((blob: Blob) => {
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `report_${p.id}.md`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      message.success('下载成功');
    }).catch(() => message.error('下载失败'));
  };


  const [polling, setPolling] = useState(false);

  const handleDetail = async (p: any) => {
    setDetailOpen(true);
    setDetailLoading(true);
    try { setDetail(await getProjectDetail(p.id)); } catch { message.error('获取详情失败'); setDetailOpen(false); }
    setDetailLoading(false);
    try {
      setReportLoading(true);
      const r = await getReport(p.id);
      setReport(r);
    } catch {} finally { setReportLoading(false); }
    if (p.status === 'pending' || p.status === 'running') {
      setPolling(true);
    }
  };

  useEffect(() => {
    if (!polling || !detail) return;
    const pid = detail.project.id;
    const timer = setInterval(async () => {
      try {
        const d = await getProjectDetail(pid);
        setDetail(d);
        if (d.project.status !== 'pending' && d.project.status !== 'running') {
          setPolling(false);
        }
      } catch {}
    }, 3000);
    return () => { clearInterval(timer); setPolling(false); };
  }, [polling, detail?.project?.id]);

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) return;
    Modal.confirm({
      title: '批量删除', icon: <ExclamationCircleOutlined />,
      content: `确认删除 ${selectedRowKeys.length} 个项目？不可恢复。`,
      okButtonProps: { danger: true },
      onOk: async () => {
        await deleteProjects(selectedRowKeys as number[]);
        message.success(`已删除 ${selectedRowKeys.length} 个项目`);
        setSelectedRowKeys([]);
        fetch();
      },
    });
  };

  const handleDelete = (p: any) => {
    Modal.confirm({
      title: '确认删除', icon: <ExclamationCircleOutlined />,
      content: `删除「${p.title}」？不可恢复。`,
      okButtonProps: { danger: true },
      onOk: async () => { await deleteProject(p.id); message.success('已删除'); fetch(); },
    });
  };

  const statusTag = (s: string) => {
    const c = statusConfig[s] || statusConfig.pending;
    return <Tag icon={c.icon} color={c.color}>{c.text}</Tag>;
  };

  const columns = [
    { title: '项目名称', dataIndex: 'title', key: 'title', width: 300,
      render: (t: string, r: any) => <a onClick={() => handleDetail(r)}>{t}</a> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 120, render: statusTag },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180,
      render: (t: string) => t ? new Date(t).toLocaleString('zh-CN') : '-' },
    { title: '操作', key: 'actions', width: 180,
      render: (_: any, r: any) => (
        <Space>
          <Button type="link" size="small" icon={<BarChartOutlined />} onClick={() => handleDetail(r)}>详情</Button>
          <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(r)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col><Title level={4} style={{ margin: 0 }}><ThunderboltOutlined style={{ marginRight: 8, color: '#1A7DFF' }} />研究任务管理</Title></Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetch}>刷新</Button>
            {selectedRowKeys.length > 0 && (
              <Button danger icon={<DeleteOutlined />} onClick={handleBatchDelete}>删除 {selectedRowKeys.length} 项</Button>
            )}
            <Button type="primary" icon={<PlusOutlined />} style={{ background: '#1A7DFF' }} onClick={() => setModalOpen(true)}>新建研究任务</Button>
          </Space>
        </Col>
      </Row>

      <Card><Table dataSource={projects} columns={columns} rowKey="id" loading={loading} pagination={{ pageSize: 10 }}
        rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
      /></Card>

      <Modal title={<Space><PlusOutlined /> 创建研究任务</Space>} open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} width={600} okText="提交">
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="研究标题" rules={[{ required: true }]}><Input placeholder="如：某科技公司深度研报" /></Form.Item>
          <Form.Item name="description" label="详细需求"><TextArea rows={3} placeholder="描述研究需求、关注的行业/公司..." /></Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="scenario" label="研究场景" initialValue="financial_research">
                <Select options={[{ value: 'financial_research', label: '行业研究' }, { value: 'competitor', label: '竞品分析' }, { value: 'investment', label: '投资研究' }]} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="output_format" label="输出格式" initialValue="markdown">
                <Select options={[{ value: 'markdown', label: 'Markdown' }, { value: 'pdf', label: 'PDF' }]} />
              </Form.Item>
            </Col>
          </Row>
          <Alert message="提交后，调度Agent将自动拆解任务并分配给多个专业Agent协同执行" type="info" showIcon icon={<RobotOutlined />} />
        </Form>
      </Modal>

      <Modal title={<Space>{polling && <SyncOutlined spin style={{ color: '#1A7DFF' }} />}任务详情</Space>} open={detailOpen} onCancel={() => { setDetailOpen(false); setDetail(null); setPolling(false); }} footer={null} width={800}>
        <Spin spinning={detailLoading}>
          {detail && (
            <div>
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="项目名称" span={2}>{detail.project.title}</Descriptions.Item>
                <Descriptions.Item label="状态">{statusTag(detail.project.status)}</Descriptions.Item>
                <Descriptions.Item label="创建时间">{new Date(detail.project.created_at).toLocaleString('zh-CN')}</Descriptions.Item>
              </Descriptions>
              <div style={{ marginTop: 16, marginBottom: 8 }}>
                <Progress
                  percent={Math.round(
                    detail.agent_statuses.length
                      ? (detail.agent_statuses.filter((a: any) => a.status === 'success' || a.status === 'failed').length / detail.agent_statuses.length) * 100
                      : 0
                  )}
                  status={detail.project.status === 'failed' ? 'exception' : 'active'}
                  strokeColor="#1A7DFF"
                />
              </div>
              <Divider />
              {report && (
                <div style={{ marginBottom: 16 }}>
                  <Title level={5} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span><FileTextOutlined style={{ marginRight: 8 }} />研究报告</span>
                    <Button icon={<DownloadOutlined />} onClick={() => handleDownload(detail.project)}>下载报告</Button>
                  </Title>
                  <Spin spinning={reportLoading}>
                    <Card size="small" style={{ background: '#fafafa', maxHeight: 400, overflow: 'auto' }}>
                      {report.sections && report.sections.length > 0 ? (
                        report.sections.map((s: any, i: number) => (
                          <div key={i} style={{ marginBottom: 12 }}>
                            <Text strong style={{ fontSize: 15 }}>{s.title}</Text>
                            <div style={{ whiteSpace: 'pre-wrap', color: '#555', marginTop: 4 }}>{s.content}</div>
                          </div>
                        ))
                      ) : (
                        <Text type="secondary">{report.content || '暂无报告内容'}</Text>
                      )}
                    </Card>
                  </Spin>
                </div>
              )}
              <Divider />
              <Title level={5}>Agent 执行状态</Title>
              <Row gutter={[8, 8]} style={{ marginBottom: 16 }}>
                {(() => {
                  const agentLabels: Record<string, string> = {
                    chief_architect: '总架构师', deep_scout: '深度侦察', chief_data_engineer: '数据工程师',
                    data_analyst: '数据分析师', chief_researcher: '首席研究', critic_master: '评论家',
                  };
                  return detail.agent_statuses.map((as: any, i: number) => {
                    const c = statusConfig[as.status] || statusConfig.pending;
                    return (
                      <Col span={8} key={i}>
                        <Card size="small"><Space>
                          <RobotOutlined style={{ fontSize: 20, color: c.color }} />
                          <div><div style={{ fontWeight: 500 }}>{agentLabels[as.agent_name] || as.agent_name}</div><Tag color={c.color}>{c.text}</Tag></div>
                        </Space></Card>
                      </Col>
                    );
                  });
                })()}
              </Row>
              <Title level={5}>子任务</Title>
              <Table dataSource={detail.tasks} columns={[
                { title: 'Agent', dataIndex: 'agent_name', key: 'agent_name' },
                { title: '状态', dataIndex: 'status', key: 'status', width: 120, render: statusTag },
              ]} rowKey="id" size="small" pagination={false} />
            </div>
          )}
        </Spin>
      </Modal>
    </div>
  );
};

export default TaskManage;
