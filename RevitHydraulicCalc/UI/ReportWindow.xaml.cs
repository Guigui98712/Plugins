using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using RevitHydraulicCalc.Core;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace RevitHydraulicCalc.UI
{
    /// <summary>
    /// Janela de relatório hidráulico
    /// </summary>
    public partial class ReportWindow : Window
    {
        private readonly HydraulicAnalysisResult _result;
        private readonly Document _doc;
        private readonly UIDocument _uiDoc;

        public ReportWindow(HydraulicAnalysisResult result, Document doc, UIDocument uiDoc)
        {
            InitializeComponent();
            _result = result;
            _doc = doc;
            _uiDoc = uiDoc;
            LoadData();
        }

        private void LoadData()
        {
            // Header info
            TxtSystemInfo.Text = $"Sistema: {_result.SystemName}  |  Análise: {_result.AnalysisDate:dd/MM/yyyy HH:mm}";
            TxtSourcePressure.Text = $"{_result.SourcePressureKPa:F2} kPa";
            TxtOutletCount.Text = _result.Nodes.Count(n => n.IsOutlet).ToString();
            TxtCriticalCount.Text = _result.Nodes.Count(n => n.IsCritical).ToString();
            TxtSegmentCount.Text = _result.SegmentResults.Count.ToString();

            // Trechos
            DgResults.ItemsSource = _result.SegmentResults;

            // Nós com status formatado
            var nodeViews = _result.Nodes.Select(n => new NodeViewModel
            {
                Id = n.Id,
                Name = n.Name,
                ElevationM = n.ElevationM,
                PressureKPa = n.PressureKPa,
                PressureMca = n.PressureMca,
                MinRequiredPressureKPa = n.MinRequiredPressureKPa,
                IsCritical = n.IsCritical,
                IsOutlet = n.IsOutlet,
                StatusText = n.IsCritical ? "CRÍTICO" : (n.IsOutlet ? "OK" : "—"),
                RevitElementId = n.RevitElementId
            }).ToList();

            DgNodes.ItemsSource = nodeViews;
        }

        private void BtnExportCsv_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                var dialog = new Microsoft.Win32.SaveFileDialog
                {
                    Filter = "CSV files (*.csv)|*.csv",
                    FileName = $"Relatorio_Hidraulico_{_result.SystemName}_{DateTime.Now:yyyyMMdd_HHmmss}.csv"
                };

                if (dialog.ShowDialog() != true) return;

                using var writer = new StreamWriter(dialog.FileName, false, System.Text.Encoding.UTF8);

                // Cabeçalho
                writer.WriteLine("Relatório Hidráulico - Calculadora de Pressão e Vazão");
                writer.WriteLine($"Sistema: {_result.SystemName}");
                writer.WriteLine($"Data: {_result.AnalysisDate:dd/MM/yyyy HH:mm:ss}");
                writer.WriteLine($"Pressão na Origem: {_result.SourcePressureKPa:F2} kPa");
                writer.WriteLine();

                // Trechos
                writer.WriteLine("TRECHOS");
                writer.WriteLine("Trecho;Nó Inicial;Nó Final;Diâmetro(mm);Comprimento(m);Vazão(L/s);Velocidade(m/s);" +
                    "Perda Distribuída(kPa);Perda Localizada(kPa);Perda Elevação(kPa);Perda Total(kPa);" +
                    "Pressão Inicial(kPa);Pressão Final(kPa)");

                foreach (var seg in _result.SegmentResults)
                {
                    writer.WriteLine($"{seg.SegmentName};{seg.StartNodeName};{seg.EndNodeName};" +
                        $"{seg.DiameterMm:F1};{seg.LengthM:F2};{seg.FlowRateLps:F3};{seg.VelocityMs:F2};" +
                        $"{seg.DistributedLossKPa:F2};{seg.LocalizedLossKPa:F2};{seg.ElevationLossKPa:F2};" +
                        $"{seg.TotalLossKPa:F2};{seg.StartPressureKPa:F2};{seg.EndPressureKPa:F2}");
                }

                writer.WriteLine();

                // Nós
                writer.WriteLine("PONTOS DE SAÍDA");
                writer.WriteLine("Nome;Elevação(m);Pressão(kPa);Pressão(mca);Mínimo Requerido(kPa);Status");

                foreach (var node in _result.Nodes.Where(n => n.IsOutlet).OrderBy(n => n.Name))
                {
                    string status = node.IsCritical ? "CRÍTICO" : "OK";
                    writer.WriteLine($"{node.Name};{node.ElevationM:F2};{node.PressureKPa:F2};" +
                        $"{node.PressureMca:F2};{node.MinRequiredPressureKPa:F2};{status}");
                }

                writer.WriteLine();

                // Alertas
                if (_result.CriticalWarnings.Any())
                {
                    writer.WriteLine("ALERTAS");
                    foreach (var warning in _result.CriticalWarnings)
                    {
                        writer.WriteLine(warning);
                    }
                }

                MessageBox.Show($"Relatório exportado com sucesso!\n\n{dialog.FileName}", 
                    "Exportação Concluída", MessageBoxButton.OK, MessageBoxImage.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Erro ao exportar: {ex.Message}", "Erro", 
                    MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        private void BtnHighlightCritical_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                var criticalNodes = _result.Nodes.Where(n => n.IsCritical && n.RevitElementId.HasValue).ToList();
                if (!criticalNodes.Any())
                {
                    MessageBox.Show("Nenhum ponto crítico encontrado para destacar.", 
                        "Info", MessageBoxButton.OK, MessageBoxImage.Information);
                    return;
                }

                var elementIds = criticalNodes
                    .Select(n => new ElementId(n.RevitElementId.Value))
                    .ToList();

                // Destaca na vista ativa com cor vermelha
                var view = _doc.ActiveView;
                var overrideSettings = new OverrideGraphicSettings();
                overrideSettings.SetProjectionLineColor(new Color(255, 0, 0));
                overrideSettings.SetProjectionLineWeight(5);
                overrideSettings.SetSurfaceForegroundPatternColor(new Color(255, 100, 100));
                overrideSettings.SetSurfaceForegroundPatternId(
                    FillPatternElement.GetFillPatternElementByName(_doc, FillPatternTarget.Drafting, "<Solid fill>")?.Id 
                    ?? ElementId.InvalidElementId);

                using (var tx = new Transaction(_doc, "Destacar Pontos Críticos"))
                {
                    tx.Start();
                    foreach (var id in elementIds)
                    {
                        try
                        {
                            view.SetElementOverrides(id, overrideSettings);
                        }
                        catch { /* ignora elementos que não podem ser sobrepostos */ }
                    }
                    tx.Commit();
                }

                // Seleciona os elementos críticos
                _uiDoc.Selection.SetElementIds(elementIds);

                MessageBox.Show($"{criticalNodes.Count} ponto(s) crítico(s) destacado(s) em vermelho na vista atual.", 
                    "Destaque Aplicado", MessageBoxButton.OK, MessageBoxImage.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Erro ao destacar: {ex.Message}", "Erro", 
                    MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        private void BtnClose_Click(object sender, RoutedEventArgs e)
        {
            Close();
        }
    }

    /// <summary>
    /// ViewModel para exibição de nós na UI
    /// </summary>
    public class NodeViewModel
    {
        public string Id { get; set; } = string.Empty;
        public string Name { get; set; } = string.Empty;
        public double ElevationM { get; set; }
        public double PressureKPa { get; set; }
        public double PressureMca { get; set; }
        public double MinRequiredPressureKPa { get; set; }
        public bool IsCritical { get; set; }
        public bool IsOutlet { get; set; }
        public string StatusText { get; set; } = string.Empty;
        public int? RevitElementId { get; set; }
    }
}
