#entry point for deepforest model
import os
import numpy as np
import pandas as pd
from skimage import io
import torch

import pytorch_lightning as pl
from torch import optim

from deepforest import utilities
from deepforest import dataset
from deepforest import get_data
from deepforest import model
from deepforest import predict
from deepforest import evaluate as evaluate_iou
from deepforest import visualize

class deepforest(pl.LightningModule):
    """Class for training and predicting tree crowns in RGB images
    """
    def __init__(self, num_classes=1, saved_model=None):
        """
        Args:
            num_classes (int): number of classes in the model
        Returns:
            self: a deepforest pytorch ligthning module
        """
        super().__init__()
                
        # Read config file - if a config file exists in local dir use it,
        # if not use installed.
        if os.path.exists("deepforest_config.yml"):
            config_path = "deepforest_config.yml"
        else:
            try:
                config_path = get_data("deepforest_config.yml")
            except Exception as e:
                raise ValueError(
                    "No deepforest_config.yml found either in local "
                    "directory or in installed package location. {}".format(e))

        print("Reading config file: {}".format(config_path))
        self.config = utilities.read_config(config_path)

        # release version id to flag if release is being used
        self.__release_version__ = None
        
        if saved_model:
            utilities.load_saved_model(saved_model)                
        else:
            self.num_classes = num_classes            
            self.create_model()

    def use_release(self):
        """Use the latest DeepForest model release from github and load model.
        Optionally download if release doesn't exist.
        Returns:
            model (object): A trained keras model
        """
        # Download latest model from github release
        release_tag, self.weights = utilities.use_release()

        # load saved model and tag release
        self.__release_version__ = release_tag
        print("Loading pre-built model: {}".format(release_tag))
    
    def create_model(self):
        """Define a deepforest retinanet architecture"""
        self.model = model.create_model(self.num_classes)
    
    def create_trainer(self, logger=None, callbacks=None, **kwargs):
        """Create a pytorch ligthning training by reading config files
        Args:
            callbacks (list): a list of pytorch-lightning callback classes
        """
        
        self.trainer = pl.Trainer(
            logger=logger,
            max_epochs=self.config["train"]["epochs"],
            gpus=self.config["gpus"],
            checkpoint_callback=False,
            distributed_backend=self.config["distributed_backend"],
            fast_dev_run=self.config["train"]["fast_dev_run"],
            callbacks=callbacks,
            **kwargs
        )        
    
    def run_train(self):
        self.trainer.fit(self)

    def load_dataset(self, csv_file, root_dir=None, augment=False, shuffle=True, batch_size=1):
        """Create a tree dataset for inference
        Csv file format is .csv file with the columns "image_path", "xmin","ymin","xmax","ymax" for the image name and bounding box position. 
        Image_path is the relative filename, not absolute path, which is in the root_dir directory. One bounding box per line. 
        
        Args:
            csv_file: path to csv file 
            root_dir: directory of images. If none, uses "image_dir" in config
            augment: Whether to create a training dataset, this activates data augmentations
        Returns:
            ds: a pytorch dataset
        """
        
        ds = dataset.TreeDataset(csv_file=csv_file,
                              root_dir=root_dir,
                              transforms=dataset.get_transform(augment=augment))
        
        data_loader = torch.utils.data.DataLoader(ds,
                                                  batch_size=batch_size,
                                                  shuffle=shuffle,
                                                  collate_fn=utilities.collate_fn,
                                                  num_workers=self.config["workers"])
        
        return data_loader
    
    def train_dataloader(self):
        loader = self.load_dataset(csv_file=self.config["train"]["csv_file"], root_dir=self.config["train"]["root_dir"], augment=True, shuffle=True, batch_size=self.config["batch_size"])
        
        return loader
    
    def test_dataloader(self):
        loader = self.load_dataset(csv_file=self.config["validation"]["csv_file"], root_dir=self.config["validation"]["root_dir"], augment=False, shuffle=False, batch_size=self.config["batch_size"])
        
        return loader
    
    def val_dataloader(self):
        loader = self.load_dataset(csv_file=self.config["validation"]["csv_file"], root_dir=self.config["validation"]["root_dir"], augment=False, shuffle=False, batch_size=self.config["batch_size"])
        
        return loader    
    
    def predict_image(self, image=None, path=None, return_plot=False,score_threshold=0.01):
        """Predict an image with a deepforest model
        
        Args:
            image: a numpy array of a RGB image ranged from 0-255
            path: optional path to read image from disk instead of passing image arg
            return_plot: Return image with plotted detections
            score_threshold: float [0,1] minimum probability score to return/plot.
        Returns:
            boxes: A pandas dataframe of predictions (Default)
            img: The input with predictions overlaid (Optional)
        """
        if isinstance(image,str):
            raise ValueError("Path provided instead of image. If you want to predict an image from disk, is path =")
  
        if path:
            if not isinstance(path,str):
                raise ValueError("Path expects a string path to image on disk")            
            image = io.imread(path)
        
            #Load on GPU is available
        if torch.cuda.is_available:
            self.model.to(self.device)   
            
        self.model.eval()   
        
        #Check if GPU is available and pass image to gpu
        result = predict.predict_image(model =  self.model, image = image, return_plot = return_plot, score_threshold = score_threshold, device=self.device)
        
        return result
                                                 
    def predict_file(self, csv_file, root_dir, savedir=None):
        """Create a dataset and predict entire annotation file
        
        Csv file format is .csv file with the columns "image_path", "xmin","ymin","xmax","ymax" for the image name and bounding box position. 
        Image_path is the relative filename, not absolute path, which is in the root_dir directory. One bounding box per line. 
        
        Args:
            csv_file: path to csv file 
            root_dir: directory of images. If none, uses "image_dir" in config
            savedir: Optional. Directory to save image plots.
        Returns:
            df: pandas dataframe with bounding boxes, label and scores for each image in the csv file
        """
        
        loader = self.load_dataset(csv_file=csv_file, root_dir=root_dir, batch_size=self.config["batch_size"])
        df = self.trainer.test(self, loader)
        result = df[0]["gathered_results"]
        
        if savedir:
            ground_truth = pd.read_csv(csv_file)
            utilities.check_file(ground_truth)
            visualize.plot_prediction_dataframe(result, ground_truth=ground_truth, root_dir=root_dir, savedir=savedir)
            
        return result
    
    def predict_tile(self,
                     raster_path=None,
                     image=None,
                     patch_size=400,
                     patch_overlap=0.05,
                     iou_threshold=0.15,
                     score_threshold=0,
                     return_plot=False,
                     use_soft_nms = False,
                     sigma = 0.5,
                     thresh = 0.001):
        """For images too large to input into the model, predict_tile cuts the
        image into overlapping windows, predicts trees on each window and
        reassambles into a single array.
        
        Args:
            raster_path: Path to image on disk
            image (array): Numpy image array in BGR channel order
                following openCV convention
            patch_size: patch size default400,
            patch_overlap: patch overlap default 0.15,
            iou_threshold: Minimum iou overlap among predictions between
                windows to be suppressed. Defaults to 0.5.
                Lower values suppress more boxes at edges.
            return_plot: Should the image be returned with the predictions drawn?
            use_soft_nms: whether to perform Gaussian Soft NMS or not, if false, default perform NMS. 
            sigma: variance of Gaussian function used in Gaussian Soft NMS
            thresh: the score thresh used to filter bboxes after soft-nms performed
        
        Returns:
            boxes (array): if return_plot, an image.
            Otherwise a numpy array of predicted bounding boxes, scores and labels
        """
        
        self.model.eval()

        result = predict.predict_tile(
            model = self.model,
            raster_path=raster_path,
            image=image,
            patch_size=patch_size,
            patch_overlap=patch_overlap,
            iou_threshold=iou_threshold,
            score_threshold=score_threshold,
            return_plot=return_plot,
            use_soft_nms = use_soft_nms,
            sigma = sigma,
            thresh = thresh,
            device=self.device
        )
        
        return result

    def training_step(self, batch, batch_idx):
        """Train on a loaded dataset
        """        
        path, images, targets = batch
        
        loss_dict = self.model.forward(images, targets)
        
        #sum of regression and classification loss        
        losses = sum([loss for loss in loss_dict.values()])
        
        return losses
    
    def validation_step(self, batch, batch_idx):
        """Train on a loaded dataset

        """
        path, images, targets = batch
        
        self.model.train()
        loss_dict = self.model.forward(images, targets)
        
        #sum of regression and classification loss
        losses = sum([loss for loss in loss_dict.values()])
        
        #Log loss
        for key, value in loss_dict.items():
            self.log("val_{}".format(key),value, on_epoch=True)
                
        return losses
    
    def test_step(self, batch, batch_idx):
        path, images, targets = batch
        predictions = self.model.forward(images)
        
        result = []
        for index, prediction in enumerate(predictions):
            formatted_prediction = visualize.format_predictions(prediction)
            formatted_prediction["image_path"] = path[index]
            result.append(formatted_prediction)
        result = pd.concat(result)
        
        return {"predictions":result}
    
    def test_epoch_end(self, outputs):
        """At the end of testing loop, gather all outputs into a single dataframe"""
        gathered = list()
        for batch in outputs:
            gathered.append(batch["predictions"])
        gathered = pd.concat(gathered)
        
        ground_df = pd.read_csv(self.config["validation"]["csv_file"])
        
        precision, recall = evaluate_iou.evaluate(
            predictions=gathered,
            ground_df=ground_df,
            root_dir=self.config["validation"]["root_dir"],
            project=self.config["validation"]["project"],
            iou_threshold=self.config["validation"]["iou_threshold"],
            score_threshold=self.config["validation"]["score_threshold"],
            show_plot=False)
        
        self.log("test_precision", precision)
        self.log("test_recall",recall)
        
        try:
            self.logger.experiment.log_metric("test_precision",precision)
            self.logger.experiment.log_metric("test_recall",recall)
        except Exception as e:
            print("test epoch could not find logger {}".format(e))

        return {"gathered_results": gathered}
        
    def validation_end(self, outputs):
        avg_loss = torch.stack([x['val_loss'] for x in outputs]).mean()
        comet_logs = {'val_loss': avg_loss}

        return {'avg_val_loss': avg_loss, 'log': comet_logs}
        
    def configure_optimizers(self):
        self.optimizer = optim.SGD(self.model.parameters(), lr=self.config["train"]["lr"], momentum=0.9)
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, mode='min', 
                                                           factor=0.1, patience=10, 
                                                           verbose=False, threshold=0.0001, 
                                                           threshold_mode='rel', cooldown=0, 
                                                           min_lr=0, eps=1e-08)
        return self.optimizer
            
    def evaluate(self, csv_file, root_dir, iou_threshold=0.5, score_threshold=0, show_plot=False, project=False):
        """Compute intersection-over-union and precision/recall for a given iou_threshold
        
        Args:
            df: a pandas-type dataframe (geopandas is fine) with columns "name","xmin","ymin","xmax","ymax","label", each box in a row
            root_dir: location of files in the dataframe 'name' column.
            iou_threshold: float [0,1] intersection-over-union union between annotation and prediction to be scored true positive
            score_threshold: float [0,1] minimum probability score to be included in evaluation
            project: Logical. Whether to project predictions that are in image coordinates (0,0 origin) into the geographic coordinates of the ground truth image. The CRS is take from the image file using rasterio.crs
            show_plot: open a blocking matplotlib window to show plot and annotations, useful for debugging.
        Returns:
            results: tuple of (precision, recall) for a given threshold
        """
        predictions = self.predict_file(csv_file, root_dir)
        ground_df = pd.read_csv(csv_file)
        
        results = evaluate_iou.evaluate(
            predictions=predictions,
            ground_df=ground_df,
            root_dir=root_dir,
            project=project,
            iou_threshold=iou_threshold,
            score_threshold=score_threshold,
            show_plot=show_plot)
        
        return results