import React, { useState, useEffect } from 'react';
import {
  Card, Button, Form, Input, Select, Space, Table, Tag, Modal, message, Typography, Row, Col,
  Descriptions, Spin, Divider, Alert, Progress,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, RobotOutlined, BarChartOutlined, FileTextOutlined,
  ThunderboltOutlined, DeleteOutlined, ExclamationCircleOutlined, DownloadOutlined,
  CheckCircleOutlined, CloseCircleOutlined, SyncOutlined, ClockCircleOutlined,
  SendOutlined, MessageOutlined,
} from '@ant-design/icons';
import { getProjects, getProjectDetail, createProject, deleteProject, deleteProjects, downloadReport } from '../services/api';
import { getReport, followupProject } from '../services/api';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

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
  const [followupQ, setFollowupQ] = useState('');
  const [followupPolling, setFollowupPolling] = useState(false);
  const [followupBase, setFollowupBase] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalItems, setTotalItems] = useState(0);

  const fetch = async (page: number = 1) => {
    setLoading(true);
    try {
      const data = await getProjects(page, 10);
      setProjects(data.items || []);
      setTotalItems(data.total || 0);
      setCurrentPage(data.page || page);
    } catch { message.error('获取失败'); }
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
      fetch(1);
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

  useEffect(() => {
    if (!followupPolling || !detail) return;
    const pid = detail.project.id;
    const timer = setInterval(async () => {
      try {
        const d = await getProjectDetail(pid);
        setDetail(d);
        const turns = (d.tasks || []).filter((t: any) => (t.agent_name || '').startsWith('followup_'));
        if (turns.length > followupBase) {
          setFollowupPolling(false);
        }
      } catch {}
    }, 3000);
    return () => clearInterval(timer);
  }, [followupPolling, detail?.project?.id, followupBase]);

  const handleFollowup = async (p: any) => {
    const q = followupQ.trim();
    if (!q) return;
    try {
      await followupProject(p.id, q);
      message.success('追问已提交，Agent 正在执行');
      setFollowupQ('');
      setFollowupBase((detail?.tasks || []).filter((t: any) => (t.agent_name || '').startsWith('followup_')).length);
      setFollowupPolling(true);
    } catch { message.error('提交失败'); }
  };

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
        fetch(currentPage);
      },
    });
  };

  const handleDelete = (p: any) => {
    Modal.confirm({
      title: '确认删除', icon: <ExclamationCircleOutlined />,
      content: `删除「${p.title}」？不可恢复。`,
      okButtonProps: { danger: true },
      onOk: async () => { await deleteProject(p.id); message.success('已删除'); fetch(currentPage); },
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
            <Button icon={<ReloadOutlined />} onClick={() => fetch(currentPage)}>刷新</Button>
            {selectedRowKeys.length > 0 && (
              <Button danger icon={<DeleteOutlined />} onClick={handleBatchDelete}>删除 {selectedRowKeys.length} 项</Button>
            )}
            <Button type="primary" icon={<PlusOutlined />} style={{ background: '#1A7DFF' }} onClick={() => setModalOpen(true)}>新建研究任务</Button>
          </Space>
        </Col>
      </Row>

      <Card><Table dataSource={projects} columns={columns} rowKey="id" loading={loading}
        pagination={{
          current: currentPage,
          pageSize: 10,
          total: totalItems,
          showSizeChanger: false,
          onChange: (page) => fetch(page),
        }}
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

      <Modal title={<Space>{polling && <SyncOutlined spin style={{ color: '#1A7DFF' }} />}任务详情</Space>} open={detailOpen} onCancel={() => { setDetailOpen(false); setDetail(null); setPolling(false); setFollowupPolling(false); setFollowupQ(''); }} footer={null} width={800}>
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
                      <div style={{ color: '#555', fontSize: 13 }}>
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          urlTransform={(url: string) =>
                            /^data:image\//i.test(url) || /^(https?|ircs?|mailto|xmpp):/i.test(url) || !url.includes(':')
                              ? url
                              : ''}
                          components={{
                            img: ({ node, ...props }) => (
                              <img {...props} style={{ maxWidth: '100%', display: 'block', margin: '8px 0' }} alt="" />
                            ),
                            h1: ({ children }) => <div style={{ fontSize: 18, fontWeight: 600, margin: '12px 0 6px' }}>{children}</div>,
                            h2: ({ children }) => <div style={{ fontSize: 16, fontWeight: 600, margin: '12px 0 6px' }}>{children}</div>,
                            h3: ({ children }) => <div style={{ fontSize: 14, fontWeight: 600, margin: '10px 0 4px' }}>{children}</div>,
                            h4: ({ children }) => <div style={{ fontSize: 13, fontWeight: 600, margin: '8px 0 4px' }}>{children}</div>,
                            p: ({ children }) => <div style={{ margin: '6px 0', lineHeight: 1.7 }}>{children}</div>,
                            table: ({ children }) => (
                              <div style={{ overflowX: 'auto' }}>
                                <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>{children}</table>
                              </div>
                            ),
                            th: ({ children }) => <th style={{ border: '1px solid #ddd', padding: '4px 8px', background: '#f0f0f0' }}>{children}</th>,
                            td: ({ children }) => <td style={{ border: '1px solid #ddd', padding: '4px 8px' }}>{children}</td>,
                          }}
                        >
                          {report.content || ''}
                        </ReactMarkdown>
                      </div>
                    </Card>
                  </Spin>
                </div>
              )}
              <Divider />
              <Title level={5}><MessageOutlined style={{ marginRight: 8 }} />多轮追问</Title>
              {(detail.tasks || []).filter((t: any) => (t.agent_name || '').startsWith('followup_')).map((t: any, i: number) => (
                <div key={t.id} style={{ marginBottom: 12 }}>
                  <Text strong style={{ color: '#1A7DFF' }}>追问 {i + 1}: {t.output_data?.question}</Text>
                  <Card size="small" style={{ background: '#fafafa', maxHeight: 400, overflow: 'auto', marginTop: 4 }}>
                    <div style={{ whiteSpace: 'pre-wrap', color: '#555' }}>{t.output_data?.answer || '(暂无内容)'}</div>
                  </Card>
                </div>
              ))}
              <Card size="small" style={{ background: '#f0f7ff' }}>
                <Space direction="vertical" style={{ width: '100%' }} size={8}>
                  <TextArea rows={2} placeholder="如：分析一下该公司未来的分红能力"
                    value={followupQ} onChange={e => setFollowupQ(e.target.value)}
                    disabled={followupPolling || detail.project.status === 'running' || detail.project.status === 'pending'} />
                  <Button type="primary" icon={<SendOutlined />} loading={followupPolling}
                    style={{ background: '#1A7DFF' }} block
                    onClick={() => handleFollowup(detail.project)}>提交追问</Button>
                </Space>
              </Card>
              <Divider />
              <Title level={5}>Agent 执行状态</Title>
              <Row gutter={[8, 8]} style={{ marginBottom: 16 }}>
                {(() => {
                  const agentLabels: Record<string, string> = {
                    supervisor: '团队负责人',
                    chief_analyst: '首席分析师',
                    industry_researcher: '行业研究员',
                    data_engineer: '数据工程师',
                    quant_analyst: '量化分析师',
                    fundamental_analyst: '基本面分析师',
                    sentiment_analyst: '情绪分析师',
                    news_analyst: '新闻分析师',
                    technical_analyst: '技术分析师',
                    senior_researcher: '高级研究员',
                    compliance_officer: '合规审核官',
                  };
                  return detail.agent_statuses.map((as: any, i: number) => {
                    const c = statusConfig[as.status] || statusConfig.pending;
                    const dur = as.duration_s != null ? `${as.duration_s}s` : '';
                    return (
                      <Col span={8} key={i}>
                        <Card size="small"><Space>
                          <RobotOutlined style={{ fontSize: 20, color: c.color }} />
                          <div><div style={{ fontWeight: 500 }}>{agentLabels[as.agent_name] || as.agent_name}</div>
                            <Space size={4}><Tag color={c.color}>{c.text}</Tag>{dur && <span style={{ color: '#999', fontSize: 12 }}>{dur}</span>}</Space>
                          </div>
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
