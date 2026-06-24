import React, { useEffect, useState } from 'react';
import { Layout, Menu, Typography, Badge } from 'antd';
import { ThunderboltOutlined, OrderedListOutlined } from '@ant-design/icons';
import { Outlet, useNavigate } from 'react-router-dom';
import { getDashboard } from '../services/api';

const { Header, Sider, Content } = Layout;

const MainLayout: React.FC = () => {
  const navigate = useNavigate();
  const [running, setRunning] = useState(0);

  useEffect(() => {
    const t = setInterval(async () => {
      try { const d = await getDashboard(); setRunning(d.tasks_by_status.running); } catch {}
    }, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} style={{ background: '#1A1A2E' }}>
        <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 18, fontWeight: 700 }}>
          <ThunderboltOutlined style={{ marginRight: 8, color: '#1A7DFF' }} />
          FinResearch
        </div>
        <Menu mode="inline" theme="dark" style={{ background: 'transparent' }}
          items={[
            { key: '/', icon: <OrderedListOutlined />, label: <span>研究任务 {running > 0 && <Badge count={running} size="small" />}</span> },
          ]}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#1A1A2E', padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography.Text style={{ color: '#fff', fontSize: 16 }}>金融深度研究平台</Typography.Text>
        </Header>
        <Content style={{ margin: 16, padding: 24, background: '#fff', borderRadius: 8, minHeight: 280 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default MainLayout;
